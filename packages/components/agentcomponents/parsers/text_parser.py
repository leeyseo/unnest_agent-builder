"""텍스트/마크다운 파서 — txt, md 등 플레인 텍스트 파일."""
from __future__ import annotations

from pathlib import Path

from agentsdk import Block, Component, NormalizedDocument, RawFile, port
from agentengine import BAD_INPUT, EngineError


class TextParser(Component):
    """txt/md 파일을 문단(빈 줄) 단위 블록으로 나눈다."""

    display_name = "텍스트 파서"
    category = "parsers"
    icon = "align-left"

    file: RawFile = port(input=True, display_name="텍스트 파일")
    document: NormalizedDocument = port(output=True, display_name="문서")

    def run(self) -> NormalizedDocument:
        if self.file is None:
            raise EngineError("파일 입력이 연결되지 않았습니다.", BAD_INPUT)
        raw = Path(self.file.path).read_bytes()
        text = None
        for enc in ("utf-8", "cp949", "euc-kr"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise EngineError(
                f"'{self.file.filename}'의 인코딩을 해석할 수 없습니다 (utf-8/cp949 시도).",
                BAD_INPUT,
            )
        blocks = [
            Block(type="text", content=para.strip(), meta={"para": i + 1})
            for i, para in enumerate(text.split("\n\n"))
            if para.strip()
        ]
        if not blocks:
            raise EngineError(f"'{self.file.filename}'가 비어 있습니다.", BAD_INPUT)
        return NormalizedDocument(
            doc_type="text", source=self.file.filename, blocks=blocks
        )
