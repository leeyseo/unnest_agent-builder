"""법령 조문 청커 — "제N조(제목)" 경계로 잘라 조문 단위 청크를 만든다.

조문 번호가 provenance에 남아 답변 인용에 쓰인다 (예: 도로교통법 제32조).
"""
from __future__ import annotations

import re

from agentsdk import Chunk, Component, NormalizedDocument, param, port
from agentengine import BAD_INPUT, EngineError

# 제12조, 제12조의2, 제 12 조(제목) 등 — 줄 시작에서만 (본문 속 "제7조를 위반" 참조 제외)
_ARTICLE = re.compile(r"^[ \t]*(제\s*\d+\s*조(?:의\s*\d+)?)\s*(?:\(([^)]*)\))?", re.MULTILINE)


class ArticleChunker(Component):
    """조문("제N조") 경계로 문서를 나눈다. 법령·규정·내규 문서 전용."""

    display_name = "조문 청커 (법령)"
    category = "chunkers"
    icon = "scale"

    document: NormalizedDocument = port(input=True, display_name="문서")
    chunks: list[Chunk] = port(output=True, display_name="청크")

    max_chars: int = param(default=2000, display_name="조문당 최대 문자 (초과 시 분할)")

    def run(self) -> list[Chunk]:
        if self.document is None:
            raise EngineError("문서 입력이 연결되지 않았습니다.", BAD_INPUT)
        full = "\n".join(b.content for b in self.document.blocks if b.content.strip())

        matches = list(_ARTICLE.finditer(full))
        if not matches:
            raise EngineError(
                "문서에서 조문(제N조) 패턴을 찾지 못했습니다. "
                "일반 문서라면 단순/문장 청커를 사용하세요.",
                BAD_INPUT,
            )

        limit = max(int(self.max_chars or 2000), 200)
        chunks: list[Chunk] = []
        seq = 0
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full)
            body = full[m.start() : end].strip()
            article_no = re.sub(r"\s+", "", m.group(1))  # "제 32 조" → "제32조"
            title = (m.group(2) or "").strip()
            # 조문이 너무 길면 max_chars 단위로 분할하되 article_no는 유지
            for j in range(0, len(body), limit):
                piece = body[j : j + limit].strip()
                if not piece:
                    continue
                chunks.append(
                    Chunk(
                        text=piece,
                        meta={
                            "source": self.document.source,
                            "seq": seq,
                            "article_no": article_no,
                            "article_title": title,
                            "part": j // limit if len(body) > limit else 0,
                        },
                    )
                )
                seq += 1
        return chunks
