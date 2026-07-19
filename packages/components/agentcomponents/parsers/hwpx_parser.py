"""HWPX 파서 — 한글(hwpx)은 zip+XML이라 표준 라이브러리만으로 텍스트를 뽑는다.

구형 .hwp(바이너리)는 미지원 — 한글에서 .hwpx로 저장하거나 PDF로 변환해야 한다.
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

from agentsdk import Block, Component, NormalizedDocument, RawFile, port
from agentengine import BAD_INPUT, EngineError


class HWPXParser(Component):
    """한글(.hwpx) 문서에서 문단 텍스트를 추출한다."""

    display_name = "HWPX 파서"
    category = "parsers"
    icon = "file-text"

    file: RawFile = port(input=True, display_name="HWPX 파일")
    document: NormalizedDocument = port(output=True, display_name="문서")

    def run(self) -> NormalizedDocument:
        if self.file is None:
            raise EngineError("파일 입력이 연결되지 않았습니다.", BAD_INPUT)
        try:
            zf = zipfile.ZipFile(self.file.path)
        except zipfile.BadZipFile as ex:
            raise EngineError(
                f"'{self.file.filename}'는 hwpx(zip) 형식이 아닙니다. "
                "구형 .hwp라면 한글에서 .hwpx 또는 PDF로 저장해 주세요.",
                BAD_INPUT,
            ) from ex

        section_names = sorted(
            n for n in zf.namelist()
            if re.match(r"Contents/section\d+\.xml", n)
        )
        if not section_names:
            raise EngineError(
                f"'{self.file.filename}' 안에서 본문(section*.xml)을 찾지 못했습니다.",
                BAD_INPUT,
            )

        blocks: list[Block] = []
        para_no = 0
        for name in section_names:
            root = ET.fromstring(zf.read(name))
            # 네임스페이스와 무관하게 문단(p) 아래 텍스트(t) 요소를 수집
            for p in root.iter():
                if not p.tag.endswith("}p") and p.tag != "p":
                    continue
                texts = [
                    t.text for t in p.iter()
                    if (t.tag.endswith("}t") or t.tag == "t") and t.text
                ]
                content = "".join(texts).strip()
                if content:
                    para_no += 1
                    blocks.append(Block(type="text", content=content, meta={"para": para_no}))

        if not blocks:
            raise EngineError(
                f"'{self.file.filename}'에서 텍스트를 추출하지 못했습니다.", BAD_INPUT
            )
        return NormalizedDocument(doc_type="hwpx", source=self.file.filename, blocks=blocks)
