"""에이전트 런타임 셸 — engine 패키지 + 얇은 FastAPI (CLAUDE.md 10절).

flow JSON 1개를 로드해 /run 으로 서빙한다. stateless (원칙 3) — 이력 저장 없음.
기동 시 KBMeta의 임베딩 모델을 대조하고 다르면 즉시 실패한다 (원칙 5).
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agentsdk import ComponentRegistry, ExecutionContext
from agentengine import Executor, Flow

FLOW_PATH = os.environ.get("FLOW_PATH", "/flows/agent.json")
RUNTIME_API_KEY = os.environ.get("RUNTIME_API_KEY", "")

_registry = ComponentRegistry()
_registry.scan_package("agentcomponents")
_flow = Flow.model_validate(json.loads(Path(FLOW_PATH).read_text(encoding="utf-8")))


def _kb_resolver(kb_id: str) -> dict:
    """번들 KB는 1개 — 접속정보는 전부 환경변수 계약으로 온다."""
    try:
        return {
            "uri": os.environ["NEO4J_URI"],
            "user": os.environ.get("NEO4J_USER", "neo4j"),
            "password": os.environ["NEO4J_PASSWORD"],
        }
    except KeyError as ex:
        raise RuntimeError(f"환경변수 {ex}가 설정되지 않았습니다 (.env 확인)") from ex


def _secret_resolver(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise KeyError(
            f"자격증명 '{name}'에 해당하는 환경변수가 없습니다 — .env에 {name}=... 을 추가하세요."
        )
    return value


def _verify_kb_on_startup() -> None:
    """KB가 뜰 때까지 재시도 후 임베딩 모델 대조. 불일치면 기동 실패 (원칙 5)."""
    kb_ids = {
        str(n.params["kb_id"])
        for n in _flow.nodes
        if n.params.get("kb_id")
    }
    if not kb_ids:
        return
    from agentcomponents.graphdb.neo4j_common import check_kb_meta, open_driver

    ctx = ExecutionContext(kb_resolver=_kb_resolver)
    deadline = time.time() + 120
    for kb_id in kb_ids:
        while True:
            try:
                driver = open_driver(ctx, kb_id)
                try:
                    check_kb_meta(driver, kb_id)
                finally:
                    driver.close()
                break
            except Exception as ex:
                if "임베딩 모델 불일치" in str(ex):
                    print(f"[FATAL] {ex}", file=sys.stderr)
                    sys.exit(1)
                if time.time() > deadline:
                    print(f"[FATAL] KB '{kb_id}' 대기 시간 초과: {ex}", file=sys.stderr)
                    sys.exit(1)
                time.sleep(3)


_verify_kb_on_startup()

app = FastAPI(title=f"Agent Runtime — {_flow.name}", version="0.1.0")


class RunBody(BaseModel):
    input: dict = {}
    stream: bool = True


def _check_key(x_api_key: str | None) -> None:
    if RUNTIME_API_KEY and x_api_key != RUNTIME_API_KEY:
        raise HTTPException(status_code=401, detail="x-api-key가 올바르지 않습니다")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "flow": _flow.name}


@app.post("/run")
def run(body: RunBody, x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)
    ctx = ExecutionContext(
        run_input=body.input, kb_resolver=_kb_resolver, secret_resolver=_secret_resolver
    )

    if not body.stream:
        result = Executor(_registry).run(_flow, context=ctx)
        return result

    q: queue.Queue = queue.Queue()

    def work() -> None:
        result = Executor(_registry).run(_flow, context=ctx, on_event=q.put)
        q.put({"__done__": result})

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = q.get()
            if "__done__" in item:
                yield f"data: {json.dumps({'event': 'result', **item['__done__']}, ensure_ascii=False, default=str)}\n\n"
                break
            yield f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
