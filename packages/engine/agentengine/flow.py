"""Flow JSON 스키마 (CLAUDE.md 5절). 캔버스 저장물 = 배포 아티팩트 = 런타임 입력."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class FlowNode(BaseModel):
    id: str
    type: str
    params: dict = Field(default_factory=dict)


class FlowEdge(BaseModel):
    # ["노드id", "포트명"]
    from_: list[str] = Field(alias="from", min_length=2, max_length=2)
    to: list[str] = Field(min_length=2, max_length=2)

    model_config = {"populate_by_name": True}


# flow params 안에 비밀값이 섞여 들어오는 것을 막는 방어선 (원칙 2)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),          # OpenAI 계열 키
    re.compile(r"AKIA[0-9A-Z]{16}"),                # AWS access key
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),  # JWT
]
_SECRET_KEY_HINT = re.compile(r"(?i)(password|passwd|api[_-]?key$|secret[_-]?key|token$)")


def find_secret_leaks(params: dict) -> list[str]:
    """params 안에서 비밀값으로 의심되는 항목 경로를 반환한다."""
    leaks: list[str] = []

    def walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}" if path else str(k)
                if isinstance(v, str) and v:
                    if any(rx.search(v) for rx in _SECRET_VALUE_PATTERNS):
                        leaks.append(p)
                    elif _SECRET_KEY_HINT.search(str(k)) and len(v) >= 8:
                        leaks.append(p)
                walk(v, p)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    walk(params, "")
    return leaks


class Flow(BaseModel):
    version: str = "1"
    name: str = "untitled"
    nodes: list[FlowNode] = Field(default_factory=list)
    edges: list[FlowEdge] = Field(default_factory=list)
    ui: dict = Field(default_factory=dict)  # 실행에 관여하지 않음 (런타임은 무시)

    @field_validator("nodes")
    @classmethod
    def _no_secret_in_params(cls, nodes: list[FlowNode]) -> list[FlowNode]:
        for n in nodes:
            leaks = find_secret_leaks(n.params)
            if leaks:
                raise ValueError(
                    f"노드 '{n.id}'의 params에 비밀값으로 의심되는 항목이 있습니다: {leaks}. "
                    "비밀값은 자격증명 저장소에 등록하고 이름만 참조하세요 (원칙 2)."
                )
        return nodes
