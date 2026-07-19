"""검색 결과를 사람이 읽는 Message로 변환 — LLM 없이 검색 flow를 채팅으로 테스트."""
from __future__ import annotations

from agentsdk import Component, Message, RetrievalHit, param, port
from agentengine import BAD_INPUT, EngineError


class RetrievalPreview(Component):
    """RetrievalHit 목록을 출처·유사도와 함께 텍스트로 정리한다."""

    display_name = "검색 결과 미리보기"
    category = "formatters"
    icon = "list"

    hits: list[RetrievalHit] = port(input=True, display_name="검색 결과")
    message: Message = port(output=True, display_name="메시지")

    max_chars: int = param(default=500, display_name="발췌당 최대 문자 (0=전체)")

    def run(self) -> Message:
        if self.hits is None:
            raise EngineError("검색 결과 입력이 연결되지 않았습니다.", BAD_INPUT)
        if not self.hits:
            return Message(text="검색 결과가 없습니다. KB에 문서가 적재되어 있는지 확인하세요.")
        limit = int(self.max_chars or 0)
        lines: list[str] = [f"검색 결과 {len(self.hits)}건:"]
        for i, h in enumerate(self.hits, start=1):
            text = h.text if limit <= 0 else h.text[:limit] + ("…" if len(h.text) > limit else "")
            pages = ",".join(map(str, h.provenance.get("pages") or [])) or "?"
            lines.append(
                f"\n[{i}] {h.provenance.get('doc_title', '?')} p.{pages} (유사도 {h.score:.3f})\n{text}"
            )
        return Message(text="\n".join(lines))
