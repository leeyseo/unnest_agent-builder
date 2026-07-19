"""청크+벡터를 Neo4j에 적재한다. 그래프 스키마는 CLAUDE.md 9절."""
from __future__ import annotations

from agentsdk import Chunk, Component, IngestReport, param, port
from agentengine import BAD_INPUT, EngineError

from .neo4j_common import check_kb_meta, open_driver


class Neo4jWriter(Component):
    """(:Document)-[:HAS_CHUNK]->(:Chunk {embedding}) 구조로 적재한다."""

    display_name = "Neo4j 적재"
    category = "graphdb"
    icon = "database"

    chunks: list[Chunk] = port(input=True, display_name="임베딩된 청크")
    report: IngestReport = port(output=True, display_name="적재 리포트")

    kb_id: str = param(default="", display_name="KB ID", required=True)

    def run(self) -> IngestReport:
        if not self.kb_id:
            raise EngineError("kb_id 파라미터가 비어 있습니다 — KB를 선택하세요.", BAD_INPUT)
        if not self.chunks:
            raise EngineError("적재할 청크가 없습니다.", BAD_INPUT)
        missing = [i for i, c in enumerate(self.chunks) if c.embedding is None]
        if missing:
            raise EngineError(
                f"embedding이 없는 청크가 {len(missing)}개 있습니다 — "
                "Neo4j 적재 앞에 임베더를 연결하세요.",
                BAD_INPUT,
            )

        source = self.chunks[0].meta.get("source", "unknown")
        driver = open_driver(self.context, self.kb_id)
        try:
            check_kb_meta(driver, self.kb_id)
            with driver.session() as session:
                # 같은 문서 재등록 시 기존 청크 교체 (kb_id 필터 필수 — 원칙 4)
                session.run(
                    """
                    MATCH (d:Document {kb_id: $kb_id, source: $source})-[:HAS_CHUNK]->(c:Chunk {kb_id: $kb_id})
                    DETACH DELETE c
                    """,
                    kb_id=self.kb_id, source=source,
                )
                result = session.run(
                    """
                    MERGE (d:Document {kb_id: $kb_id, source: $source})
                    SET d.title = $title
                    WITH d
                    UNWIND $rows AS row
                    CREATE (c:Chunk {kb_id: $kb_id, text: row.text, seq: row.seq,
                                     pages: row.pages, embedding: row.embedding})
                    CREATE (d)-[:HAS_CHUNK]->(c)
                    RETURN count(c) AS written
                    """,
                    kb_id=self.kb_id,
                    source=source,
                    title=source,
                    rows=[
                        {
                            "text": c.text,
                            "seq": c.meta.get("seq", i),
                            "pages": c.meta.get("pages", []),
                            "embedding": c.embedding,
                        }
                        for i, c in enumerate(self.chunks)
                    ],
                ).single()
                written = result["written"] if result else 0
            return IngestReport(
                kb_id=self.kb_id, chunks_written=written, nodes_created=written + 1
            )
        finally:
            driver.close()
