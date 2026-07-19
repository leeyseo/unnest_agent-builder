"""M0 검증: 더미 컴포넌트 flow 실행 + 이벤트 + 타입검증 + 비밀값 방어선."""
from __future__ import annotations

import pytest
from agentsdk import Component, ComponentRegistry, Message, port, param
from agentengine import Executor, Flow
from pydantic import ValidationError


class Upper(Component):
    """텍스트를 대문자로."""

    display_name = "Upper"
    category = "test"

    inp: Message = port(input=True)
    out: Message = port(output=True)

    def run(self) -> Message:
        return Message(text=self.inp.text.upper())


class Emit(Component):
    """고정 텍스트 발신."""

    display_name = "Emit"
    category = "test"

    out: Message = port(output=True)
    text: str = param(default="hello")

    def run(self) -> Message:
        return Message(text=self.text)


class Boom(Component):
    display_name = "Boom"
    category = "test"

    inp: Message = port(input=True)
    out: Message = port(output=True)

    def run(self) -> Message:
        raise RuntimeError("boom")


@pytest.fixture
def registry() -> ComponentRegistry:
    r = ComponentRegistry()
    for cls in (Upper, Emit, Boom):
        r.register(cls)
    return r


def make_flow(nodes, edges) -> Flow:
    return Flow.model_validate({"version": "1", "name": "t", "nodes": nodes, "edges": edges})


def test_linear_flow_runs_and_emits_events(registry):
    flow = make_flow(
        [{"id": "a", "type": "Emit", "params": {"text": "안녕"}},
         {"id": "b", "type": "Upper", "params": {}}],
        [{"from": ["a", "out"], "to": ["b", "inp"]}],
    )
    events = []
    result = Executor(registry).run(flow, on_event=events.append)
    assert result["status"] == "ok"
    assert result["outputs"]["b"]["text"] == "안녕".upper()
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    assert "node_started" in kinds and "node_finished" in kinds


def test_type_mismatch_fails_before_run(registry):
    class EmitRaw(Component):
        display_name = "EmitRaw"
        category = "test"
        out: int = port(output=True)

        def run(self) -> int:
            return 1

    registry.register(EmitRaw)
    flow = make_flow(
        [{"id": "a", "type": "EmitRaw", "params": {}},
         {"id": "b", "type": "Upper", "params": {}}],
        [{"from": ["a", "out"], "to": ["b", "inp"]}],
    )
    problems = Executor(registry).validate(flow)
    assert problems and "타입 불일치" in problems[0]


def test_failed_node_skips_downstream(registry):
    flow = make_flow(
        [{"id": "a", "type": "Emit", "params": {}},
         {"id": "b", "type": "Boom", "params": {}},
         {"id": "c", "type": "Upper", "params": {}}],
        [{"from": ["a", "out"], "to": ["b", "inp"]},
         {"from": ["b", "out"], "to": ["c", "inp"]}],
    )
    events = []
    result = Executor(registry).run(flow, on_event=events.append)
    assert result["status"] == "failed"
    by_node = {e.get("node_id"): e["event"] for e in events if e.get("node_id")}
    assert by_node["b"] == "node_failed"
    assert by_node["c"] == "node_skipped"
    failed = next(e for e in events if e["event"] == "node_failed")
    assert failed["error_kind"] == "component_bug"
    assert "traceback" in failed


def test_cycle_detected(registry):
    flow = make_flow(
        [{"id": "a", "type": "Upper", "params": {}},
         {"id": "b", "type": "Upper", "params": {}}],
        [{"from": ["a", "out"], "to": ["b", "inp"]},
         {"from": ["b", "out"], "to": ["a", "inp"]}],
    )
    from agentengine import FlowValidationError
    with pytest.raises(FlowValidationError):
        Executor(registry)._toposort(flow)


def test_secret_in_params_rejected():
    with pytest.raises(ValidationError):
        Flow.model_validate({
            "version": "1", "name": "bad",
            "nodes": [{"id": "n1", "type": "OpenAICompatLLM",
                       "params": {"api_key": "sk-abcdefghijklmnop1234567890"}}],
            "edges": [],
        })


def test_spec_introspection():
    spec = Upper.spec()
    assert spec["type"] == "Upper"
    assert spec["inputs"] == [{"name": "inp", "type": "Message", "display_name": "inp"}]
    assert spec["outputs"][0]["type"] == "Message"
