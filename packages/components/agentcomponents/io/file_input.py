"""파일 업로드 슬롯 — ingest flow의 진입점."""
from __future__ import annotations

from pathlib import Path

from agentsdk import Component, RawFile, port
from agentengine import BAD_INPUT, EngineError


class FileInput(Component):
    """업로드된 파일을 RawFile로 내보낸다."""

    display_name = "파일 입력"
    category = "io"
    icon = "upload"

    file: RawFile = port(output=True, display_name="파일")

    def run(self) -> RawFile:
        ri = self.context.run_input
        f = ri.get("file")
        if not f:
            raise EngineError(
                '실행 입력에 file이 없습니다. 문서 등록 API로 실행하거나 '
                'run 요청 body에 {"input": {"file": {"path": "...", "mime": "...", "filename": "..."}}} 를 넣어주세요.',
                BAD_INPUT,
            )
        raw = RawFile(**f) if isinstance(f, dict) else f
        if not Path(raw.path).exists():
            raise EngineError(f"파일이 존재하지 않습니다: {raw.path}", BAD_INPUT)
        return raw
