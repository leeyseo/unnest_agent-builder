"""여러 검색 노드의 결과를 하나로 병합 — 멀티 KB / 멀티 전략 조합용."""
from __future__ import annotations

from agentsdk import Component, RetrievalHit, param, port
from agentengine import BAD_INPUT, EngineError

from .search import rrf_fuse


class HitsMerger(Component):
    """검색 결과 2~3개를 병합한다 (서로 다른 KB 또는 서로 다른 전략)."""

    display_name = "검색 결과 병합"
    category = "graphdb"
    icon = "git-merge"

    hits_a: list[RetrievalHit] = port(input=True, display_name="검색 결과 A")
    hits_b: list[RetrievalHit] = port(input=True, display_name="검색 결과 B")
    hits_c: list[RetrievalHit] = port(input=True, display_name="검색 결과 C (선택)")
    merged: list[RetrievalHit] = port(output=True, display_name="병합 결과")

    top_k: int = param(default=5, display_name="최종 개수")
    mode: str = param(
        default="rrf",
        display_name="병합 방식",
        choices=["rrf", "score"],  # rrf=순위 융합(전략 혼합에 적합), score=점수순(동일 전략에 적합)
    )

    def run(self) -> list[RetrievalHit]:
        lists = [h for h in (self.hits_a, self.hits_b, self.hits_c) if h]
        if not lists:
            raise EngineError(
                "병합할 검색 결과가 없습니다 — 검색 노드를 A/B 입력에 연결하세요.", BAD_INPUT
            )
        k = int(self.top_k)
        if self.mode == "score":
            # 같은 전략끼리는 점수 스케일이 같으므로 점수순 병합이 자연스럽다
            all_hits = [hit for hits in lists for hit in hits]
            return sorted(all_hits, key=lambda h: h.score, reverse=True)[:k]
        return rrf_fuse(lists, top_k=k)
