"""문장 경계 기반 청커 — 문장을 자르지 않고 max_chars까지 묶는다."""
from __future__ import annotations

import re

from agentsdk import Chunk, Component, NormalizedDocument, param, port
from agentengine import BAD_INPUT, EngineError

# 한국어/영어 문장 종결 추정: 마침표류 + 종결어미(다./요.) 뒤 공백
_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|(?<=[다요]\.)\s+")


class SentenceChunker(Component):
    """문장 단위로 나눈 뒤 max_chars까지 묶는다 (문장이 중간에 잘리지 않음)."""

    display_name = "문장 청커"
    category = "chunkers"
    icon = "pilcrow"

    document: NormalizedDocument = port(input=True, display_name="문서")
    chunks: list[Chunk] = port(output=True, display_name="청크")

    max_chars: int = param(default=800, display_name="청크 최대 문자")
    overlap_sentences: int = param(default=1, display_name="겹침 문장 수")

    def run(self) -> list[Chunk]:
        if self.document is None:
            raise EngineError("문서 입력이 연결되지 않았습니다.", BAD_INPUT)
        limit = max(int(self.max_chars or 800), 100)
        ov = max(int(self.overlap_sentences or 0), 0)

        # (문장, 페이지) 목록으로 평탄화
        sentences: list[tuple[str, object]] = []
        for block in self.document.blocks:
            page = block.meta.get("page")
            for sent in _SENT_SPLIT.split(block.content):
                sent = sent.strip()
                if sent:
                    sentences.append((sent, page))
        if not sentences:
            raise EngineError("문서에서 문장을 찾지 못했습니다 (빈 문서).", BAD_INPUT)

        chunks: list[Chunk] = []
        buf: list[tuple[str, object]] = []
        seq = 0

        def flush() -> None:
            nonlocal seq, buf
            if not buf:
                return
            pages = sorted({p for _, p in buf if p})
            chunks.append(
                Chunk(
                    text=" ".join(s for s, _ in buf),
                    meta={"source": self.document.source, "pages": pages, "seq": seq},
                )
            )
            seq += 1
            buf = buf[-ov:] if ov else []  # 겹침 문장 유지

        for item in sentences:
            if buf and sum(len(s) for s, _ in buf) + len(item[0]) > limit:
                flush()
            buf.append(item)
        flush()
        return chunks
