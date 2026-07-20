"""표 파서 — PDF의 표를 감지해 type="table" 블록으로 문서에 추가한다.

보강 파서(enricher) 체인 패턴: 기본 파서(PDFParser) 뒤에 연결해 표 블록을 더한다.
document 입력을 연결하지 않으면 표 블록만 담긴 문서를 새로 만든다 (단독 사용 가능).
"""
from __future__ import annotations

from agentsdk import Block, Component, NormalizedDocument, RawFile, param, port
from agentengine import BAD_INPUT, EngineError


def merge_blocks_by_page(*block_lists: list[Block]) -> list[Block]:
    """여러 파서가 만든 블록을 페이지 순서로 안정 병합한다 (페이지 정보 없는 블록은 뒤)."""
    indexed = []
    for li, blocks in enumerate(block_lists):
        for bi, b in enumerate(blocks):
            page = b.meta.get("page")
            indexed.append((page if page is not None else 10**9, li, bi, b))
    indexed.sort(key=lambda t: (t[0], t[1], t[2]))
    return [t[3] for t in indexed]


def _to_markdown(rows: list[list[str | None]] | None) -> str:
    """표 셀 행렬 → 마크다운 표. 행 구조가 보존돼 청커가 통째로 다룰 수 있다."""
    clean = [[(c or "").strip().replace("\n", " ") for c in row] for row in rows or []]
    clean = [r for r in clean if any(r)]
    if not clean:
        return ""
    header, *body = clean
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


class TableParser(Component):
    """PDF의 표를 추출해 표 블록을 추가한다 — 기본 파서 뒤에 체인으로 연결."""

    display_name = "표 파서 (PDF)"
    category = "parsers"
    icon = "table"

    file: RawFile = port(input=True, display_name="PDF 파일")
    document: NormalizedDocument = port(input=True, display_name="문서 (앞 파서 출력, 선택)")
    enriched: NormalizedDocument = port(output=True, display_name="표 추가된 문서")

    max_pages: int = param(default=0, display_name="최대 페이지 (0=전체)")

    def run(self) -> NormalizedDocument:
        import fitz  # pymupdf

        if self.file is None:
            raise EngineError("PDF 파일 입력이 연결되지 않았습니다.", BAD_INPUT)
        try:
            pdf = fitz.open(self.file.path)
        except Exception as ex:
            raise EngineError(
                f"PDF를 열 수 없습니다: {self.file.filename} — {ex}. "
                "파일이 손상되었거나 암호화되어 있을 수 있습니다.",
                BAD_INPUT,
            ) from ex

        table_blocks: list[Block] = []
        try:
            total = len(pdf)
            limit = min(self.max_pages, total) if self.max_pages else total
            for pno in range(limit):
                try:
                    tabs = pdf[pno].find_tables()
                except Exception:
                    continue  # 표 감지 실패한 페이지는 건너뜀 (텍스트는 기본 파서 몫)
                for ti, tab in enumerate(tabs.tables):
                    md = _to_markdown(tab.extract())
                    if md:
                        table_blocks.append(
                            Block(type="table", content=md,
                                  meta={"page": pno + 1, "table_no": ti + 1})
                        )
        finally:
            pdf.close()

        base = self.document or NormalizedDocument(
            doc_type="pdf", source=self.file.filename, blocks=[]
        )
        return NormalizedDocument(
            doc_type=base.doc_type,
            source=base.source or self.file.filename,
            blocks=merge_blocks_by_page(base.blocks, table_blocks),
            meta={**base.meta, "tables_found": len(table_blocks)},
        )
