"""검색 전략 공용 헬퍼 — Vector/Keyword/Hybrid 리트리버가 공유한다.

모든 쿼리에 kb_id 필터 (원칙 4).
"""
from __future__ import annotations

import re

from agentsdk import RetrievalHit


def ensure_fulltext_index(session) -> None:
    """키워드 검색용 풀텍스트 인덱스 (기존 KB에도 lazy 생성)."""
    session.run(
        "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text]"
    )


def vector_search(session, kb_id: str, query_vec: list[float], top_k: int) -> list[RetrievalHit]:
    records = session.run(
        """
        CALL db.index.vector.queryNodes('chunk_embedding', $fetch_k, $vec)
        YIELD node, score
        WHERE node.kb_id = $kb_id
        MATCH (d:Document {kb_id: $kb_id})-[:HAS_CHUNK]->(node)
        RETURN node.text AS text, score, node.seq AS seq,
               node.pages AS pages, node.article_no AS article_no, d.title AS doc_title
        ORDER BY score DESC
        LIMIT $top_k
        """,
        fetch_k=max(top_k * 4, 20),
        top_k=top_k,
        vec=query_vec,
        kb_id=kb_id,
    ).data()
    return [_to_hit(r, kb_id, "vector") for r in records]


def keyword_search(session, kb_id: str, query_text: str, top_k: int) -> list[RetrievalHit]:
    ensure_fulltext_index(session)
    # Lucene 특수문자 제거 후 단어 OR 검색.
    # 한국어는 조사·접미가 붙으므로 접두 일치(단어*)도 함께 건다 ("점검" → "점검용" 매칭)
    terms = re.sub(r'[+\-!(){}\[\]^"~*?:\\/]', " ", query_text).split()
    if not terms:
        return []
    lucene_query = " OR ".join(f"({t} OR {t}*)" for t in terms)
    records = session.run(
        """
        CALL db.index.fulltext.queryNodes('chunk_text', $q)
        YIELD node, score
        WHERE node.kb_id = $kb_id
        MATCH (d:Document {kb_id: $kb_id})-[:HAS_CHUNK]->(node)
        RETURN node.text AS text, score, node.seq AS seq,
               node.pages AS pages, node.article_no AS article_no, d.title AS doc_title
        ORDER BY score DESC
        LIMIT $top_k
        """,
        q=lucene_query,
        top_k=top_k,
        kb_id=kb_id,
    ).data()
    return [_to_hit(r, kb_id, "keyword") for r in records]


def rrf_fuse(result_lists: list[list[RetrievalHit]], top_k: int, k: int = 60) -> list[RetrievalHit]:
    """Reciprocal Rank Fusion — 서로 다른 전략의 순위를 합성한다."""
    scores: dict[tuple, float] = {}
    best: dict[tuple, RetrievalHit] = {}
    strategies: dict[tuple, list[str]] = {}
    for hits in result_lists:
        for rank, hit in enumerate(hits):
            key = (hit.provenance.get("kb_id"), hit.provenance.get("doc_title"),
                   hit.provenance.get("seq"))
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            best.setdefault(key, hit)
            strategies.setdefault(key, []).append(hit.provenance.get("strategy", "?"))
    fused = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]:
        hit = best[key]
        fused.append(
            RetrievalHit(
                text=hit.text,
                score=round(score, 4),
                provenance={**hit.provenance, "strategy": "+".join(sorted(set(strategies[key])))},
            )
        )
    return fused


def expand_neighbors(session, kb_id: str, hits: list[RetrievalHit], n: int) -> list[RetrievalHit]:
    """그래프 컨텍스트 확장: 히트 청크의 앞뒤 seq 청크를 이어붙인다 (HAS_CHUNK 순서 이용)."""
    if n <= 0:
        return hits
    expanded = []
    for hit in hits:
        seq = hit.provenance.get("seq")
        title = hit.provenance.get("doc_title")
        if seq is None or title is None:
            expanded.append(hit)
            continue
        records = session.run(
            """
            MATCH (d:Document {kb_id: $kb_id, title: $title})-[:HAS_CHUNK]->(c:Chunk {kb_id: $kb_id})
            WHERE c.seq >= $lo AND c.seq <= $hi
            RETURN c.text AS text, c.seq AS seq ORDER BY c.seq
            """,
            kb_id=kb_id, title=title, lo=seq - n, hi=seq + n,
        ).data()
        merged = "\n".join(r["text"] for r in records) or hit.text
        expanded.append(
            RetrievalHit(
                text=merged,
                score=hit.score,
                provenance={**hit.provenance, "expanded": f"±{n}"},
            )
        )
    return expanded


def _to_hit(record: dict, kb_id: str, strategy: str) -> RetrievalHit:
    prov = {
        "doc_title": record["doc_title"],
        "seq": record["seq"],
        "pages": record["pages"],
        "kb_id": kb_id,
        "strategy": strategy,
    }
    if record.get("article_no"):
        prov["article_no"] = record["article_no"]
    return RetrievalHit(text=record["text"], score=float(record["score"]), provenance=prov)
