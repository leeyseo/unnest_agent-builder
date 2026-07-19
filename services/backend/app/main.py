"""FastAPI 게이트웨이 (CLAUDE.md 7절). MVP: 인증 없는 로컬 단일 사용자."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

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
    return JSONResponse(
        content=data["flow"],
        headers={"Content-Disposition": f'attachment; filename="{data["name"]}.flow.json"'},
    )


class RunBody(BaseModel):
    input: dict = {}


@app.post("/api/flows/{flow_id}/run")
def run_flow(flow_id: str, body: RunBody) -> StreamingResponse:
    data = get_flow(flow_id)

    def stream():
        for ev in runner.run_flow_events(data["flow"], body.input, flow_id=flow_id):
            yield runner.sse_format(ev)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/flows/adhoc/run")
def run_adhoc(body: FlowBody) -> StreamingResponse:
    """저장 전 캔버스 상태 그대로 실행 (플레이그라운드)."""
    _validate_flow(body.flow)

    def stream():
        for ev in runner.run_flow_events(body.flow, body.flow.get("__input__", {}), flow_id=None):
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
