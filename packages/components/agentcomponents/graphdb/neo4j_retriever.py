"""벡터 인덱스 + 그래프 컨텍스트 검색. 모든 쿼리에 kb_id 필터 (원칙 4)."""
from __future__ import annotations

from agentsdk import Component, Message, RetrievalHit, param, port
from agentengine import BAD_INPUT, EngineError

from ..embeddings.local_embedder import embed_texts
from .neo4j_common import check_kb_meta, open_driver


class Neo4jRetriever(Component):
    """질문을 임베딩해 chunk_embedding 인덱스에서 top_k 청크를 찾는다."""

    display_name = "Neo4j 검색"
    category = "graphdb"
    icon = "search"

    query: Message = port(input=True, display_name="질문")
    hits: list[RetrievalHit] = port(output=True, display_name="검색 결과")

    kb_id: str = param(default="", display_name="KB ID", required=True)
    top_k: int = param(default=5, display_name="검색 개수")

    def run(self) -> list[RetrievalHit]:
        if not self.kb_id:
            raise EngineError("kb_id 파라미터가 비어 있습니다 — KB를 선택하세요.", BAD_INPUT)
        if self.query is None or not self.query.text.strip():
            raise EngineError("질문이 비어 있습니다.", BAD_INPUT)

        [query_vec] = embed_texts([self.query.text])
        driver = open_driver(self.context, self.kb_id)
        try:
            check_kb_meta(driver, self.kb_id)
            with driver.session() as session:
                records = session.run(
                    """
                    CALL db.index.vector.queryNodes('chunk_embedding', $fetch_k, $vec)
                    YIELD node, score
                    WHERE node.kb_id = $kb_id
                    MATCH (d:Document {kb_id: $kb_id})-[:HAS_CHUNK]->(node)
                    RETURN node.text AS text, score, node.seq AS seq,
                           node.pages AS pages, d.title AS doc_title
                    ORDER BY score DESC
                    LIMIT $top_k
                    """,
                    fetch_k=max(int(self.top_k) * 4, 20),  # kb_id 필터 후 부족 방지
                    top_k=int(self.top_k),
                    vec=query_vec,
                    kb_id=self.kb_id,
                ).data()
        finally:
            driver.close()

        return [
            RetrievalHit(
                text=r["text"],
                score=float(r["score"]),
                provenance={
                    "doc_title": r["doc_title"],
                    "seq": r["seq"],
                    "pages": r["pages"],
                    "kb_id": self.kb_id,
                },
            )
            for r in records
        ]
