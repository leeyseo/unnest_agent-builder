"""청커 전략 단위 테스트: 문장 경계 보존, 조문 분리, RRF 융합."""
from __future__ import annotations

import pytest
from agentcomponents.chunkers.article_chunker import ArticleChunker
from agentcomponents.chunkers.sentence_chunker import SentenceChunker
from agentcomponents.graphdb.search import rrf_fuse
from agentengine import EngineError
from agentsdk import Block, NormalizedDocument, RetrievalHit


def make_doc(*contents: str) -> NormalizedDocument:
    return NormalizedDocument(
        doc_type="text",
        source="t.txt",
        blocks=[Block(content=c, meta={"page": i + 1}) for i, c in enumerate(contents)],
    )


def test_sentence_chunker_keeps_sentence_boundaries():
    s1 = "첫 번째 문장은 청커가 문장 경계를 존중하는지 확인하기 위해 일부러 길게 작성한 문장입니다."
    s2 = "두 번째 문장도 마찬가지로 청크 최대 길이를 넘기기 위해 충분히 길게 작성한 문장입니다."
    s3 = "세 번째 문장까지 더해지면 반드시 두 개 이상의 청크로 나뉘어야 합니다."
    c = SentenceChunker(params={"max_chars": 100, "overlap_sentences": 0})
    c.set_input("document", make_doc(f"{s1} {s2} {s3}"))
    chunks = c.run()
    assert len(chunks) >= 2
    for ch in chunks:  # 문장이 중간에 잘리지 않았는지: 각 청크가 종결부호로 끝남
        assert ch.text.rstrip().endswith(".")


def test_article_chunker_splits_by_article():
    doc = make_doc(
        "제1조(목적) 이 법은 도로 교통의 안전을 목적으로 한다.\n"
        "제2조(정의) 이 법에서 사용하는 용어의 뜻은 다음과 같다.\n"
        "제32조의2(보호구역) 어린이 보호구역의 주정차를 금지한다."
    )
    c = ArticleChunker(params={"max_chars": 2000})
    c.set_input("document", doc)
    chunks = c.run()
    assert [ch.meta["article_no"] for ch in chunks] == ["제1조", "제2조", "제32조의2"]
    assert chunks[0].meta["article_title"] == "목적"
    assert "안전을 목적" in chunks[0].text


def test_article_chunker_rejects_non_law_document():
    c = ArticleChunker(params={})
    c.set_input("document", make_doc("조문이 전혀 없는 일반 문서입니다."))
    with pytest.raises(EngineError):
        c.run()


def _hit(title: str, seq: int, score: float, strategy: str) -> RetrievalHit:
    return RetrievalHit(
        text=f"{title}-{seq}", score=score,
        provenance={"doc_title": title, "seq": seq, "strategy": strategy},
    )


def test_rrf_fusion_prefers_items_in_both_lists():
    vec = [_hit("d", 1, 0.9, "vector"), _hit("d", 2, 0.8, "vector")]
    kw = [_hit("d", 2, 5.0, "keyword"), _hit("d", 3, 4.0, "keyword")]
    fused = rrf_fuse([vec, kw], top_k=3)
    assert fused[0].provenance["seq"] == 2  # 양쪽에 등장 → 최상위
    assert fused[0].provenance["strategy"] == "keyword+vector"
    assert len(fused) == 3
