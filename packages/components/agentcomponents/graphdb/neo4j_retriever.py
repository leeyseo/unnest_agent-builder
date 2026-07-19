"""검색 컴포넌트 3종 — 벡터 / 키워드 / 하이브리드(RRF). 전략을 캔버스에서 갈아끼운다."""
from __future__ import annotations

from agentsdk import Component, Message, RetrievalHit, param, port
from agentengine import BAD_INPUT, EngineError

from ..embeddings.local_embedder import embed_texts
from .neo4j_common import check_kb_meta, open_driver
from .search import expand_neighbors, keyword_search, rrf_fuse, vector_search


class _RetrieverBase(Component):
    """공통 골격: kb 검증 → 전략 실행 → (선택) 이웃 청크 확장."""

    query: Message = port(input=True, display_name="질문")
    hits: list[RetrievalHit] = port(output=True, display_name="검색 결과")

    kb_id: str = param(default="", display_name="KB ID", required=True)
    top_k: int = param(default=5, display_name="검색 개수")
    expand: int = param(default=0, display_name="이웃 청크 확장 (±n)")

    def _search(self, session) -> list[RetrievalHit]:  # 전략별 구현
        raise NotImplementedError

    def run(self) -> list[RetrievalHit]:
        if not self.kb_id:
            raise EngineError("kb_id 파라미터가 비어 있습니다 — KB를 선택하세요.", BAD_INPUT)
        if self.query is None or not self.query.text.strip():
            raise EngineError("질문이 비어 있습니다.", BAD_INPUT)
        driver = open_driver(self.context, self.kb_id)
        try:
            check_kb_meta(driver, self.kb_id)
            with driver.session() as session:
                hits = self._search(session)
                return expand_neighbors(session, self.kb_id, hits, int(self.expand))
        finally:
            driver.close()


# Component.__init_subclass__가 베이스를 레지스트리에 등록하지 않도록 표시
_RetrieverBase.abstract = True


class Neo4jRetriever(_RetrieverBase):
    """벡터 검색 — 질문 임베딩과 청크 임베딩의 cosine 유사도 (의미 기반)."""

    display_name = "벡터 검색"
    category = "graphdb"
    icon = "search"

    def _search(self, session) -> list[RetrievalHit]:
        [query_vec] = embed_texts([self.query.text])
        return vector_search(session, self.kb_id, query_vec, int(self.top_k))


class KeywordRetriever(_RetrieverBase):
    """키워드 검색 — 풀텍스트 인덱스(BM25 계열). 고유명사·조문번호에 강함."""

    display_name = "키워드 검색"
    category = "graphdb"
    icon = "type"

    def _search(self, session) -> list[RetrievalHit]:
        return keyword_search(session, self.kb_id, self.query.text, int(self.top_k))


class HybridRetriever(_RetrieverBase):
    """하이브리드 검색 — 벡터+키워드 결과를 RRF로 융합. 대부분의 경우 최선의 기본값."""

    display_name = "하이브리드 검색"
    category = "graphdb"
    icon = "layers"

    rrf_k: int = param(default=60, display_name="RRF k (순위 완충)")

    def _search(self, session) -> list[RetrievalHit]:
        k = int(self.top_k)
        [query_vec] = embed_texts([self.query.text])
        vec_hits = vector_search(session, self.kb_id, query_vec, k * 2)
        kw_hits = keyword_search(session, self.kb_id, self.query.text, k * 2)
        return rrf_fuse([vec_hits, kw_hits], top_k=k, k=int(self.rrf_k))
