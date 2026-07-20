"""기본 제공 ingest flow가 전부 엔진 검증(포트 타입·노드 존재)을 통과하는지."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentengine import Executor, Flow
from agentsdk import ComponentRegistry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "backend"))

from app.ingest import BUILTIN_INGEST_FLOWS  # noqa: E402


@pytest.fixture(scope="module")
def executor() -> Executor:
    reg = ComponentRegistry()
    reg.scan_package("agentcomponents")
    return Executor(reg)


@pytest.mark.parametrize("flow_id", list(BUILTIN_INGEST_FLOWS), ids=str)
def test_builtin_ingest_flow_valid(executor, flow_id):
    flow_dict, _exts = BUILTIN_INGEST_FLOWS[flow_id]
    flow = Flow.model_validate(flow_dict)
    problems = executor.validate(flow)
    assert problems == [], f"{flow_id}: {problems}"
    # 적재 flow 요건: 파일 진입점 + KB 기록 노드 (업로드 모달 목록에 뜨는 조건)
    types = {n.type for n in flow.nodes}
    assert "FileInput" in types and "Neo4jWriter" in types
