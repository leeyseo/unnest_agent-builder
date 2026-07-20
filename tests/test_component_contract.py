"""컴포넌트 계약 테스트 — 등록된 모든 컴포넌트가 플랫폼 규칙을 지키는지.

새 컴포넌트를 추가하면 이 테스트가 자동으로 잡아서 검증한다 (파라미터라이즈).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentsdk import ComponentRegistry
from agentsdk.validate import golden_check, validate_component

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TXT = ROOT / "samples" / "주차관리규정.txt"


def _all_components():
    reg = ComponentRegistry()
    reg.scan_package("agentcomponents")
    return [reg.get(s["type"]) for s in reg.specs()]


ALL = _all_components()


@pytest.mark.parametrize("cls", ALL, ids=lambda c: c.__name__)
def test_static_contract(cls):
    """정적 계약: 포트 타입, 카테고리 시그니처, 스펙 직렬화, 비밀값/의존성."""
    report = validate_component(cls)
    assert not report.errors, f"{cls.__name__} 계약 위반: {report.errors}"


@pytest.mark.parametrize(
    "cls",
    [c for c in ALL if c.category in ("chunkers", "formatters")],
    ids=lambda c: c.__name__,
)
def test_golden_pure(cls):
    """청커/포맷터는 외부 의존이 없으므로 샘플 입력으로 실제 실행까지 검증."""
    report = golden_check(cls)
    assert report.dynamic == "ok", f"{cls.__name__}: {report.errors or report.warnings}"


def test_golden_text_parser():
    """파서 골든 테스트 — 저장소 샘플 문서로 TextParser를 실제 실행."""
    from agentcomponents.parsers.text_parser import TextParser

    report = golden_check(TextParser, sample_file=SAMPLE_TXT)
    assert report.dynamic == "ok", f"TextParser: {report.errors}"
