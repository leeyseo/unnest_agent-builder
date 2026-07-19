"""flow JSON을 위상 정렬 순서로 실행하고 노드 단위 이벤트를 발행한다 (원칙 6)."""
from __future__ import annotations

import json
import time
import traceback as tb_module
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Callable

from agentsdk import Component, ComponentRegistry, ExecutionContext, types_compatible
from pydantic import BaseModel

from .flow import Flow

EventCallback = Callable[[dict], None]

# error_kind 분류 (CLAUDE.md 6절)
BAD_INPUT = "bad_input"
COMPONENT_BUG = "component_bug"
UPSTREAM_UNREACHABLE = "upstream_unreachable"
AUTH_FAILED = "auth_failed"
TIMEOUT = "timeout"


class FlowValidationError(Exception):
    pass


class EngineError(Exception):
    """컴포넌트가 원인 분류를 실어 던질 수 있는 예외."""

    def __init__(self, message: str, kind: str = COMPONENT_BUG):
        super().__init__(message)
        self.kind = kind


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(value: Any, limit: int = 400) -> str:
    try:
        if isinstance(value, BaseModel):
            s = value.model_dump_json()
        elif isinstance(value, list):
            s = json.dumps(
                [v.model_dump() if isinstance(v, BaseModel) else v for v in value[:5]],
                ensure_ascii=False,
                default=str,
            )
        else:
            s = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        s = repr(value)
    return s[:limit] + ("…" if len(s) > limit else "")


def _mask_secret_params(component_cls: type[Component], params: dict) -> dict:
    """관측 이벤트에 비밀값이 찍히면 안 된다 — secret 파라미터는 *** 마스킹 (원칙 2)."""
    masked = {}
    for k, v in params.items():
        decl = component_cls._params.get(k)
        masked[k] = "***" if (decl and decl.secret) else v
    return masked


class Executor:
    def __init__(self, registry: ComponentRegistry):
        self.registry = registry

    # ------------------------------------------------------------ 검증

    def validate(self, flow: Flow) -> list[str]:
        """실행 전 정적 검증. 문제 목록(비면 통과)을 반환한다."""
        problems: list[str] = []
        node_map = {n.id: n for n in flow.nodes}
        for n in flow.nodes:
            try:
                self.registry.get(n.type)
            except KeyError as e:
                problems.append(str(e))
        for e in flow.edges:
            src_id, src_port = e.from_
            dst_id, dst_port = e.to
            if src_id not in node_map or dst_id not in node_map:
                problems.append(f"엣지가 존재하지 않는 노드를 참조합니다: {src_id} → {dst_id}")
                continue
            try:
                src_cls = self.registry.get(node_map[src_id].type)
                dst_cls = self.registry.get(node_map[dst_id].type)
            except KeyError:
                continue
            if src_port not in src_cls._outputs:
                problems.append(f"'{node_map[src_id].type}'에 출력 포트 '{src_port}'가 없습니다")
                continue
            if dst_port not in dst_cls._inputs:
                problems.append(f"'{node_map[dst_id].type}'에 입력 포트 '{dst_port}'가 없습니다")
                continue
            out_t = src_cls._outputs[src_port].type_name
            in_t = dst_cls._inputs[dst_port].type_name
            if not types_compatible(out_t, in_t):
                problems.append(
                    f"타입 불일치: {src_id}.{src_port}({out_t}) → {dst_id}.{dst_port}({in_t})"
                )
        return problems

    def _toposort(self, flow: Flow) -> list[str]:
        indeg: dict[str, int] = {n.id: 0 for n in flow.nodes}
        adj: dict[str, list[str]] = defaultdict(list)
        for e in flow.edges:
            adj[e.from_[0]].append(e.to[0])
            indeg[e.to[0]] += 1
        q = deque(sorted([nid for nid, d in indeg.items() if d == 0]))
        order: list[str] = []
        while q:
            nid = q.popleft()
            order.append(nid)
            for nxt in adj[nid]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        if len(order) != len(flow.nodes):
            raise FlowValidationError("flow에 순환이 있습니다 — DAG만 실행 가능합니다")
        return order

    # ------------------------------------------------------------ 실행

    def run(
        self,
        flow: Flow,
        run_input: dict | None = None,
        context: ExecutionContext | None = None,
        on_event: EventCallback | None = None,
        run_id: str | None = None,
    ) -> dict:
        """flow를 실행하고 {"status", "run_id", "outputs"}를 반환한다.

        outputs: 종점(출력 엣지가 없는) 노드들의 결과. ChatOutput이 있으면 그 값이 답변.
        """
        run_id = run_id or f"r-{uuid.uuid4().hex[:12]}"
        emit = on_event or (lambda ev: None)
        ctx = context or ExecutionContext(run_input=run_input)
        if run_input is not None:
            ctx.run_input = run_input

        problems = self.validate(flow)
        if problems:
            emit({"run_id": run_id, "event": "run_failed", "ts": _now(),
                  "error": "; ".join(problems), "error_kind": BAD_INPUT})
            return {"status": "failed", "run_id": run_id, "error": "; ".join(problems)}

        order = self._toposort(flow)
        node_map = {n.id: n for n in flow.nodes}
        # 입력 버퍼: node_id → {port: value}
        buffers: dict[str, dict[str, Any]] = defaultdict(dict)
        outputs_by_node: dict[str, Any] = {}
        failed_nodes: set[str] = set()
        downstream: dict[str, list] = defaultdict(list)
        for e in flow.edges:
            downstream[e.from_[0]].append(e)
        has_outgoing = {e.from_[0] for e in flow.edges}

        emit({"run_id": run_id, "event": "run_started", "ts": _now(), "flow": flow.name})

        run_failed = False
        for nid in order:
            node = node_map[nid]
            cls = self.registry.get(node.type)

            # 상류 실패 → 스킵 전파
            upstream_failed = any(
                e.from_[0] in failed_nodes for e in flow.edges if e.to[0] == nid
            )
            if upstream_failed:
                failed_nodes.add(nid)
                emit({"run_id": run_id, "node_id": nid, "event": "node_skipped", "ts": _now()})
                continue

            input_snapshot = {k: _preview(v) for k, v in buffers[nid].items()}
            emit({
                "run_id": run_id, "node_id": nid, "event": "node_started", "ts": _now(),
                "node_type": node.type,
                "params": _mask_secret_params(cls, node.params),
            })
            t0 = time.perf_counter()
            try:
                comp = cls(params=node.params, context=ctx)
                for pname, pvalue in buffers[nid].items():
                    comp.set_input(pname, pvalue)
                # 필수 입력 누락 검사
                connected = set(buffers[nid])
                edges_in = {e.to[1] for e in flow.edges if e.to[0] == nid}
                missing = [p for p in cls._inputs if p in edges_in and p not in connected]
                if missing:
                    raise EngineError(
                        f"입력 포트 {missing}에 값이 도착하지 않았습니다 (상류 노드 확인)",
                        BAD_INPUT,
                    )
                result = comp.run()
            except EngineError as ex:
                run_failed = True
                failed_nodes.add(nid)
                emit({
                    "run_id": run_id, "node_id": nid, "event": "node_failed", "ts": _now(),
                    "error": str(ex), "error_kind": ex.kind,
                    "traceback": tb_module.format_exc(),
                    "input_snapshot": input_snapshot,
                })
                continue
            except Exception as ex:
                run_failed = True
                failed_nodes.add(nid)
                emit({
                    "run_id": run_id, "node_id": nid, "event": "node_failed", "ts": _now(),
                    "error": f"{type(ex).__name__}: {ex}", "error_kind": COMPONENT_BUG,
                    "traceback": tb_module.format_exc(),
                    "input_snapshot": input_snapshot,
                })
                continue

            duration_ms = int((time.perf_counter() - t0) * 1000)
            outputs_by_node[nid] = result

            # 출력 값을 엣지 따라 다음 노드 입력 버퍼에 저장
            out_ports = list(cls._outputs)
            for e in downstream[nid]:
                src_port = e.from_[1]
                if isinstance(result, dict) and not isinstance(result, BaseModel) and src_port in result:
                    value = result[src_port]
                elif len(out_ports) == 1:
                    value = result
                else:
                    value = result
                buffers[e.to[0]][e.to[1]] = value

            emit({
                "run_id": run_id, "node_id": nid, "event": "node_finished", "ts": _now(),
                "duration_ms": duration_ms, "output_preview": _preview(result),
            })

        status = "failed" if run_failed else "ok"
        # 종점 노드 출력 수집
        terminal_outputs = {
            nid: (val.model_dump() if isinstance(val, BaseModel) else _jsonable(val))
            for nid, val in outputs_by_node.items()
            if nid not in has_outgoing
        }
        emit({"run_id": run_id, "event": "run_finished", "ts": _now(),
              "status": status, "outputs": terminal_outputs})
        return {"status": status, "run_id": run_id, "outputs": terminal_outputs}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value
