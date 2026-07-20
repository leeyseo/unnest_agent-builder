"""보강 파서 체인 테스트: PDF(텍스트+표) → PDFParser → TableParser → SimpleChunker."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentsdk import RawFile


@pytest.fixture(scope="module")
def table_pdf(tmp_path_factory) -> Path:
    """텍스트 문단 + 괘선 있는 3x3 표가 들어간 PDF를 생성한다."""
    import fitz

    path = tmp_path_factory.mktemp("pdf") / "표문서.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Parking fine rules are described in the table below.",
                     fontsize=11)

    rows = [
        ["zone", "violation", "fine"],
        ["school-zone", "parking", "120000"],
        ["general", "parking", "40000"],
    ]
    x0, y0, cw, rh = 72, 90, 140, 24
    for r in range(len(rows) + 1):  # 가로 괘선
        page.draw_line((x0, y0 + r * rh), (x0 + cw * 3, y0 + r * rh))
    for c in range(4):  # 세로 괘선
        page.draw_line((x0 + c * cw, y0), (x0 + c * cw, y0 + rh * len(rows)))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((x0 + c * cw + 6, y0 + r * rh + 16), cell, fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def _raw(path: Path) -> RawFile:
    return RawFile(path=str(path), mime="application/pdf", filename=path.name)


def test_table_parser_standalone(table_pdf):
    """document 입력 없이 단독 실행 — 표 블록만 담긴 문서를 만든다."""
    from agentcomponents.parsers.table_parser import TableParser

    comp = TableParser(params={})
    comp.set_input("file", _raw(table_pdf))
    out = comp.run()
    tables = [b for b in out.blocks if b.type == "table"]
    assert len(tables) == 1
    assert "school-zone" in tables[0].content and "120000" in tables[0].content
    assert tables[0].meta["page"] == 1


def test_chain_pdf_then_table(table_pdf):
    """체인: PDFParser 출력에 TableParser가 표 블록을 병합한다."""
    from agentcomponents.parsers.pdf_parser import PDFParser
    from agentcomponents.parsers.table_parser import TableParser

    p1 = PDFParser(params={})
    p1.set_input("file", _raw(table_pdf))
    base = p1.run()
    assert any(b.type == "text" for b in base.blocks)

    p2 = TableParser(params={})
    p2.set_input("file", _raw(table_pdf))
    p2.set_input("document", base)
    out = p2.run()
    types = {b.type for b in out.blocks}
    assert types == {"text", "table"}
    assert out.meta["tables_found"] == 1
    assert out.source == table_pdf.name


def test_chunker_preserves_table_blocks(table_pdf):
    """표 블록은 잘리지 않고 통째로 청크 1개가 된다 (block_type 표시)."""
    from agentcomponents.chunkers.simple_chunker import SimpleChunker
    from agentcomponents.parsers.pdf_parser import PDFParser
    from agentcomponents.parsers.table_parser import TableParser

    p1 = PDFParser(params={})
    p1.set_input("file", _raw(table_pdf))
    p2 = TableParser(params={})
    p2.set_input("file", _raw(table_pdf))
    p2.set_input("document", p1.run())
    doc = p2.run()

    chunker = SimpleChunker(params={"chunk_size": 50, "overlap": 0})  # 일부러 작게
    chunker.set_input("document", doc)
    chunks = chunker.run()

    table_chunks = [c for c in chunks if c.meta.get("block_type") == "table"]
    assert len(table_chunks) == 1
    # 작은 chunk_size에도 표는 잘리지 않고 행 구조(마크다운)가 온전하다
    assert "| school-zone | parking | 120000 |" in table_chunks[0].text
    # 텍스트 청크도 존재하고 seq는 전체에서 유일하다
    assert any(c.meta.get("block_type") != "table" for c in chunks)
    seqs = [c.meta["seq"] for c in chunks]
    assert len(seqs) == len(set(seqs))


def test_merge_blocks_by_page():
    from agentsdk import Block
    from agentcomponents.parsers.table_parser import merge_blocks_by_page

    text = [Block(content="p1 text", meta={"page": 1}), Block(content="p3 text", meta={"page": 3})]
    tables = [Block(type="table", content="p2 table", meta={"page": 2})]
    merged = merge_blocks_by_page(text, tables)
    assert [b.content for b in merged] == ["p1 text", "p2 table", "p3 text"]
