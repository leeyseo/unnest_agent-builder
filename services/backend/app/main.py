"""FastAPI 게이트웨이 (CLAUDE.md 7절). MVP: 인증 없는 로컬 단일 사용자."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from agentengine import Flow

from . import credentials, db, ingest, provisioner, runner
from .config import UPLOAD_DIR

app = FastAPI(title="Unnest Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    ingest.ensure_default_ingest_flow()
    runner.registry()  # 컴포넌트 스캔 (실패 시 기동 로그에 바로 드러나게)


# ------------------------------------------------------------------ 컴포넌트


@app.get("/api/components")
def list_components() -> list[dict]:
    return runner.registry().specs()


@app.post("/api/components/upload")
def upload_component(file: UploadFile = File(...)) -> dict:
    """컴포넌트 .py 업로드 — 계약 검증기를 통과해야 contrib/에 등록된다.

    검증은 서브프로세스에서 격리 실행한다 (임포트 부작용이 백엔드를 오염시키지 않게).
    응답: {ok, reports, registered} — 실패해도 200으로 리포트를 돌려줘 UI가 표시한다.
    """
    import os
    import re as _re
    import subprocess
    import sys

    name = Path(file.filename or "").name
    if not name.endswith(".py"):
        raise HTTPException(status_code=422, detail="컴포넌트는 파이썬 파일(.py) 하나로 업로드하세요")
    content = file.file.read()
    if len(content) > 200_000:
        raise HTTPException(status_code=422, detail="파일이 너무 큽니다 (200KB 제한)")

    stem = _re.sub(r"[^a-zA-Z0-9_]", "_", Path(name).stem) or "component"
    tmp = UPLOAD_DIR / f"__component_{uuid.uuid4().hex[:8]}_{stem}.py"
    tmp.write_bytes(content)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "agentsdk.validate", "--json", str(tmp)],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            # Windows 콘솔 기본 인코딩(cp949)이 한글/특수문자 출력에서 깨지는 것 방지
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            tail = (proc.stderr or proc.stdout or "")[-500:]
            raise HTTPException(
                status_code=422,
                detail=f"검증기 실행 실패 — 파일이 임포트되지 않습니다: {tail}",
            ) from None
        if "load_error" in result:
            return {"ok": False, "reports": [], "load_error": result["load_error"]}
        reports = result["reports"]

        # 이름 충돌: 기존 내장 컴포넌트와 겹치면 거부, contrib 재업로드는 갱신 허용
        reg = runner.registry()
        conflicts = []
        for r in reports:
            try:
                existing = reg.get(r["component"])
            except KeyError:
                continue
            if not existing.__module__.startswith("agentcomponents.contrib"):
                conflicts.append(r["component"])
        if conflicts:
            return {
                "ok": False, "reports": reports,
                "load_error": f"이미 내장된 컴포넌트와 이름이 겹칩니다: {conflicts} — "
                              "클래스 이름을 바꿔서 업로드하세요",
            }

        if any(not r["ok"] for r in reports):
            return {"ok": False, "reports": reports}

        # 통과 → contrib 패키지에 배치 + 레지스트리 리로드
        import agentcomponents

        contrib_dir = Path(agentcomponents.__path__[0]) / "contrib"
        contrib_dir.mkdir(exist_ok=True)
        init = contrib_dir / "__init__.py"
        if not init.exists():
            init.write_text('"""업로드로 등록된 컴포넌트 (POST /api/components/upload)."""\n',
                            encoding="utf-8")
        (contrib_dir / f"{stem}.py").write_bytes(content)
        runner.reload_registry()
        return {"ok": True, "reports": reports,
                "registered": [r["component"] for r in reports]}
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------ KB


class KBCreate(BaseModel):
    name: str


@app.get("/api/kb")
def list_kb() -> list[dict]:
    return provisioner.list_kb()


@app.post("/api/kb")
def create_kb(body: KBCreate) -> dict:
    try:
        return provisioner.create_kb(body.name)
    except provisioner.ProvisionError as ex:
        raise HTTPException(status_code=502, detail=str(ex)) from ex


@app.delete("/api/kb/{kb_id}")
def delete_kb(kb_id: str) -> dict:
    try:
        provisioner.delete_kb(kb_id)
    except KeyError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    return {"ok": True}


# ------------------------------------------------------------------ Flows


class FlowBody(BaseModel):
    name: str | None = None
    flow: dict


def _validate_flow(flow_dict: dict) -> Flow:
    try:
        return Flow.model_validate(flow_dict)
    except ValidationError as ex:
        # 비밀값 오염 등 — 사용자 친화 메시지로 (원칙 2)
        msgs = [e.get("msg", str(e)) for e in ex.errors()]
        raise HTTPException(status_code=422, detail="; ".join(msgs)) from ex


@app.get("/api/flows")
def list_flows() -> list[dict]:
    rows = db.query("SELECT id, name, json, created_at, updated_at FROM flows ORDER BY updated_at DESC")
    out = []
    for row in rows:
        try:
            types = {n.get("type") for n in json.loads(row["json"]).get("nodes", [])}
        except Exception:
            types = set()
        out.append({
            "id": row["id"], "name": row["name"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            # 적재 flow 여부: 파일 진입점 + KB 기록 노드를 모두 가진 flow
            "is_ingest": "FileInput" in types and "Neo4jWriter" in types,
        })
    return out


@app.post("/api/flows")
def create_flow(body: FlowBody) -> dict:
    flow = _validate_flow(body.flow)
    flow_id = f"f-{uuid.uuid4().hex[:12]}"
    name = body.name or flow.name
    db.execute(
        "INSERT INTO flows (id, name, json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (flow_id, name, json.dumps(body.flow, ensure_ascii=False), db.now(), db.now()),
    )
    return {"id": flow_id, "name": name}


@app.get("/api/flows/{flow_id}")
def get_flow(flow_id: str) -> dict:
    rows = db.query("SELECT * FROM flows WHERE id = ?", (flow_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"flow '{flow_id}'가 없습니다")
    row = rows[0]
    return {"id": row["id"], "name": row["name"], "flow": json.loads(row["json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


@app.put("/api/flows/{flow_id}")
def update_flow(flow_id: str, body: FlowBody) -> dict:
    _validate_flow(body.flow)
    if not db.query("SELECT id FROM flows WHERE id = ?", (flow_id,)):
        raise HTTPException(status_code=404, detail=f"flow '{flow_id}'가 없습니다")
    name = body.name or body.flow.get("name", "untitled")
    db.execute(
        "UPDATE flows SET name = ?, json = ?, updated_at = ? WHERE id = ?",
        (name, json.dumps(body.flow, ensure_ascii=False), db.now(), flow_id),
    )
    return {"id": flow_id, "name": name}


@app.delete("/api/flows/{flow_id}")
def delete_flow(flow_id: str) -> dict:
    db.execute("DELETE FROM flows WHERE id = ?", (flow_id,))
    return {"ok": True}


@app.get("/api/flows/{flow_id}/export")
def export_flow(flow_id: str):
    data = get_flow(flow_id)
    # HTTP 헤더는 latin-1 전용 — 한글 flow 이름은 RFC 5987로 인코딩
    fname = quote(f"{data['name']}.flow.json")
    return JSONResponse(
        content=data["flow"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


class RunBody(BaseModel):
    input: dict = {}


# 주의: 정적 경로(adhoc)는 반드시 /{flow_id}/run 보다 먼저 선언해야 매칭된다
@app.post("/api/flows/adhoc/run")
def run_adhoc(body: FlowBody) -> StreamingResponse:
    """저장 전 캔버스 상태 그대로 실행 (플레이그라운드)."""
    _validate_flow(body.flow)

    def stream():
        for ev in runner.run_flow_events(body.flow, body.flow.get("__input__", {}), flow_id=None):
            yield runner.sse_format(ev)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/flows/{flow_id}/run")
def run_flow(flow_id: str, body: RunBody) -> StreamingResponse:
    data = get_flow(flow_id)

    def stream():
        for ev in runner.run_flow_events(data["flow"], body.input, flow_id=flow_id):
            yield runner.sse_format(ev)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ------------------------------------------------------------------ Runs


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    rows = db.query("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"run '{run_id}'가 없습니다")
    row = rows[0]
    return {**row, "events": json.loads(row["events"] or "[]")}


# ------------------------------------------------------------------ 문서 등록


@app.post("/api/documents")
def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    ingest_flow_id: str = Form(default=""),
) -> StreamingResponse:
    try:
        provisioner.get_kb(kb_id)
        provisioner.ensure_running(kb_id)
    except KeyError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    except provisioner.ProvisionError as ex:
        raise HTTPException(status_code=502, detail=str(ex)) from ex

    # ingest flow 미지정 시 확장자에 맞는 기본 flow 자동 선택
    if not ingest_flow_id:
        try:
            ingest_flow_id = ingest.flow_id_for_file(file.filename or "")
        except ValueError as ex:
            raise HTTPException(status_code=422, detail=str(ex)) from ex
    flow_data = get_flow(ingest_flow_id)
    safe_name = Path(file.filename or "upload.bin").name
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    dest.write_bytes(file.file.read())
    doc_id = ingest.register_document(kb_id, safe_name, str(dest))

    flow_with_kb = ingest.prepare_ingest_flow(flow_data["flow"], kb_id)
    run_input = {
        "file": {"path": str(dest), "mime": file.content_type or "application/octet-stream",
                 "filename": safe_name}
    }

    def stream():
        yield runner.sse_format({"event": "document_registered", "doc_id": doc_id, "kb_id": kb_id})
        chunks_written = 0
        status = "failed"
        for ev in runner.run_flow_events(flow_with_kb, run_input, flow_id=ingest_flow_id):
            if ev.get("event") == "run_finished":
                status = "done" if ev.get("status") == "ok" else "failed"
                for out in (ev.get("outputs") or {}).values():
                    if isinstance(out, dict) and "chunks_written" in out:
                        chunks_written = out["chunks_written"]
            yield runner.sse_format(ev)
        ingest.finish_document(doc_id, status, chunks_written)
        yield runner.sse_format({"event": "document_done", "doc_id": doc_id,
                                 "status": status, "chunks_written": chunks_written})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/documents")
def list_documents(kb_id: str | None = None) -> list[dict]:
    if kb_id:
        return db.query(
            "SELECT id, kb_id, filename, status, chunks_written, created_at "
            "FROM documents WHERE kb_id = ? ORDER BY created_at DESC", (kb_id,))
    return db.query(
        "SELECT id, kb_id, filename, status, chunks_written, created_at "
        "FROM documents ORDER BY created_at DESC")


# ------------------------------------------------------------------ 자격증명


class CredentialBody(BaseModel):
    name: str
    value: str


@app.post("/api/credentials")
def create_credential(body: CredentialBody) -> dict:
    credentials.store(body.name, body.value)
    return {"ok": True, "name": body.name}


@app.get("/api/credentials")
def list_credentials() -> list[str]:
    return credentials.names()


@app.delete("/api/credentials/{name}")
def delete_credential(name: str) -> dict:
    credentials.delete(name)
    return {"ok": True}


# ------------------------------------------------------------------ 번들


@app.post("/api/agents/{flow_id}/bundle")
def bundle_agent(flow_id: str) -> dict:
    from . import bundler

    try:
        return bundler.make_bundle(flow_id)
    except bundler.BundleError as ex:
        raise HTTPException(status_code=422, detail=str(ex)) from ex


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
