"""문자 길이 기반 청커. Semantic/Article(조문) 청커도 같은 패턴으로 추가된다."""
from __future__ import annotations

from agentsdk import Chunk, Component, NormalizedDocument, param, port
from agentengine import BAD_INPUT, EngineError


class SimpleChunker(Component):
    """블록을 이어붙여 chunk_size 문자 단위로 자른다 (overlap 겹침)."""

    display_name = "단순 청커"
    category = "chunkers"
    icon = "scissors"

    document: NormalizedDocument = port(input=True, display_name="문서")
    chunks: list[Chunk] = port(output=True, display_name="청크")

    chunk_size: int = param(default=800, display_name="청크 크기(문자)")
    overlap: int = param(default=100, display_name="겹침(문자)")

    def run(self) -> list[Chunk]:
        if self.document is None:
            raise EngineError("문서 입력이 연결되지 않았습니다.", BAD_INPUT)
        size = max(int(self.chunk_size or 800), 50)
        overlap = min(max(int(self.overlap or 0), 0), size - 1)

        # 표 블록은 문자 단위로 자르면 행이 깨진다 — 통째로 청크 1개씩 보존
        table_blocks = [
            b for b in self.document.blocks if b.type == "table" and b.content.strip()
        ]
        # (문자오프셋, 페이지) 매핑을 유지하며 전체 텍스트 조립 — provenance 보존
        pieces: list[tuple[str, dict]] = [
            (b.content, b.meta)
            for b in self.document.blocks
            if b.type != "table" and b.content.strip()
        ]
        full = ""
        spans: list[tuple[int, int, dict]] = []  # start, end, meta
        for content, meta in pieces:
            start = len(full)
            full += content + "\n\n"
            spans.append((start, len(full), meta))

        chunks: list[Chunk] = []
        pos = 0
        seq = 0
        while pos < len(full):
            piece = full[pos : pos + size].strip()
            if piece:
                pages = sorted(
                    {
                        s_meta.get("page")
                        for s_start, s_end, s_meta in spans
                        if s_start < pos + size and s_end > pos and s_meta.get("page")
                    }
                )
                chunks.append(
                    Chunk(
                        text=piece,
                        meta={"source": self.document.source, "pages": pages, "seq": seq},
                    )
                )
                seq += 1
            pos += size - overlap

        for tb in table_blocks:
            pages = [tb.meta["page"]] if tb.meta.get("page") else []
            chunks.append(
                Chunk(
                    text=tb.content,
                    meta={"source": self.document.source, "pages": pages,
                          "seq": seq, "block_type": "table"},
                )
            )
            seq += 1

        if not chunks:
            raise EngineError("문서에서 만들 청크가 없습니다 (빈 문서).", BAD_INPUT)
        return chunks
