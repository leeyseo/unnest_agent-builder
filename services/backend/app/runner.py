"""엔진 실행 호스트: 레지스트리 싱글턴 + SSE 스트리밍 실행."""
from __future__ import annotations

import json
import queue
import threading

from agentsdk import ComponentRegistry, ExecutionContext
from agentengine import Executor, Flow

from . import credentials, db
from .provisioner import kb_conn_info

_registry: ComponentRegistry | None = None


def registry() -> ComponentRegistry:
    global _registry
    if _registry is None:
        _registry = ComponentRegistry()
        _registry.scan_package("agentcomponents")
    return _registry


def make_context(run_input: dict | None) -> ExecutionContext:
    return ExecutionContext(
        run_input=run_input,
        kb_resolver=kb_conn_info,
        secret_resolver=credentials.resolve,
    )


def run_flow_events(flow_dict: dict, run_input: dict | None, flow_id: str | None = None):
    """flow를 백그라운드 스레드로 실행하며 이벤트를 제너레이터로 낸다 (SSE용).

    종료 후 runs 테이블에 전체 이벤트를 저장한다 (관측성 — 원칙 6).
    """
    flow = Flow.model_validate(flow_dict)
    q: queue.Queue = queue.Queue()
    events: list[dict] = []
    started_at = db.now()

    def on_event(ev: dict) -> None:
        events.append(ev)
        q.put(ev)

    def work() -> None:
        try:
            result = Executor(registry()).run(
                flow, context=make_context(run_input), on_event=on_event
            )
        except Exception as ex:  # 엔진 자체 오류
            result = {"status": "failed", "error": f"{type(ex).__name__}: {ex}", "run_id": None}
            q.put({"event": "run_failed", "error": result["error"]})
        q.put({"__done__": result})

    threading.Thread(target=work, daemon=True).start()

    result: dict = {}
    while True:
        item = q.get()
        if "__done__" in item:
            result = item["__done__"]
            break
        yield item
    if result.get("run_id"):
        db.save_run(result["run_id"], flow_id, result.get("status", "?"), started_at, events)


def sse_format(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
