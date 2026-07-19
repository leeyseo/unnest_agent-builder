"""채팅 진입/종점 컴포넌트. 런타임은 stateless — 이력은 호출자가 실어 보낸다 (원칙 3)."""
from __future__ import annotations

from agentsdk import Component, Message, port
from agentengine import BAD_INPUT, EngineError


class ChatInput(Component):
    """채팅 진입점 — 실행 요청의 text/history를 Message로 내보낸다."""

    display_name = "채팅 입력"
    category = "io"
    icon = "message-circle"

    message: Message = port(output=True, display_name="메시지")

    def run(self) -> Message:
        ri = self.context.run_input
        text = ri.get("text") or ri.get("message") or ""
        if not text:
            raise EngineError(
                "실행 입력에 text가 없습니다. 채팅 패널에서 질문을 입력하거나 "
                'run 요청 body에 {"input": {"text": "..."}} 를 넣어주세요.',
                BAD_INPUT,
            )
        return Message(text=text, history=ri.get("history", []))


class ChatOutput(Component):
    """채팅 종점 — 도착한 Message가 곧 최종 답변이다."""

    display_name = "채팅 출력"
    category = "io"
    icon = "message-square"

    message: Message = port(input=True, display_name="메시지")

    def run(self) -> Message:
        if self.message is None:
            raise EngineError("메시지 입력이 연결되지 않았습니다.", BAD_INPUT)
        return self.message
