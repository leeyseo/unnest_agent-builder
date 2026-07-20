"""PDF 파서 사본 — 컴포넌트 업로드 기능 테스트용 (내장 PDFParser와 동일 동작)."""
from __future__ import annotations

from agentsdk import Block, Component, NormalizedDocument, RawFile, param, port
from agentengine import BAD_INPUT, EngineError


class PDFParser2(Component):
    """PDF에서 페이지 단위 텍스트 블록을 추출한다 (업로드 테스트용 사본)."""

    display_name = "PDF 파서 2"
    category = "parsers"
    icon = "file-text"

    file: RawFile = port(input=True, display_name="PDF 파일")
    document: NormalizedDocument = port(output=True, display_name="문서")

    max_pages: int = param(default=0, display_name="최대 페이지 (0=전체)")
    ocr: str = param(
        default="auto",
        display_name="OCR (텍스트 없을 때)",
        choices=["auto", "off", "force"],
    )

    def run(self) -> NormalizedDocument:
        from pypdf import PdfReader

        if self.file is None:
            raise EngineError("PDF 파일 입력이 연결되지 않았습니다.", BAD_INPUT)
        try:
            reader = PdfReader(self.file.path)
        except Exception as ex:
            raise EngineError(
                f"PDF를 열 수 없습니다: {self.file.filename} — {ex}. "
                "파일이 손상되었거나 암호화되어 있을 수 있습니다.",
                BAD_INPUT,
            ) from ex

        pages = reader.pages
        if self.max_pages and self.max_pages > 0:
            pages = pages[: self.max_pages]

        blocks: list[Block] = []
        if self.ocr != "force":
            for i, page in enumerate(pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    blocks.append(Block(type="text", content=text, meta={"page": i}))

        # 텍스트 레이어가 없는 PDF는 OCR로 폴백 (내장 파서의 OCR 모듈 재사용)
        if not blocks and self.ocr in ("auto", "force"):
            from agentcomponents.parsers.ocr import ocr_pdf_pages

            blocks = ocr_pdf_pages(self.file.path, max_pages=self.max_pages)

        if not blocks:
            raise EngineError(
                f"'{self.file.filename}'에서 텍스트를 추출하지 못했습니다. "
                "텍스트 레이어가 없고 OCR도 실패했습니다 — ocr 파라미터와 "
                "Windows 한국어 언어팩(OCR)을 확인하세요.",
                BAD_INPUT,
            )
        return NormalizedDocument(
            doc_type="pdf",
            source=self.file.filename,
            blocks=blocks,
            meta={"total_pages": len(reader.pages)},
        )
