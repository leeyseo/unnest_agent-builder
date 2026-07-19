"""OCR 헬퍼: PDF 페이지를 이미지로 렌더링해 텍스트를 뽑는다.

개발/온프레미스 Windows에서는 Windows 내장 OCR(winocr)을 쓴다.
리눅스 런타임 컨테이너용 OCR 엔진은 백로그(ImageOCRParser 교체 지점).
"""
from __future__ import annotations

import sys

from agentsdk import Block
from agentengine import BAD_INPUT, EngineError


def ocr_pdf_pages(path: str, max_pages: int = 0, lang: str = "ko", dpi: int = 200) -> list[Block]:
    if sys.platform != "win32":
        raise EngineError(
            "이 환경에는 OCR 엔진이 없습니다 (Windows OCR은 win32 전용). "
            "리눅스 런타임용 OCR 컴포넌트는 아직 백로그입니다.",
            BAD_INPUT,
        )
    import fitz  # pymupdf
    import winocr
    from PIL import Image

    doc = fitz.open(path)
    pages = range(len(doc)) if not max_pages else range(min(max_pages, len(doc)))
    blocks: list[Block] = []
    for i in pages:
        pix = doc[i].get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        try:
            result = winocr.recognize_pil_sync(img, lang)
        except Exception as ex:
            raise EngineError(
                f"Windows OCR 실행 실패 (언어팩 '{lang}' 설치 여부 확인): {ex}",
                BAD_INPUT,
            ) from ex
        text = (result.get("text") if isinstance(result, dict) else getattr(result, "text", "")) or ""
        text = text.strip()
        if text:
            blocks.append(Block(type="text", content=text, meta={"page": i + 1, "ocr": True}))
    return blocks
