"""검색 결과를 인용 정보와 함께 프롬프트로 조립한다."""
from __future__ import annotations

from agentsdk import Component, Message, RetrievalHit, param, port
from agentengine import BAD_INPUT, EngineError

DEFAULT_TEMPLATE = """당신은 제공된 문서 발췌만 근거로 답하는 조수입니다.
발췌에 없는 내용은 "문서에서 확인되지 않습니다"라고 답하세요.
답변에 근거 발췌 번호를 [1]처럼 인용하세요.

[문서 발췌]
{context}

[질문]
{question}"""


class PromptTemplate(Component):
    """{question}, {context} 플레이스홀더로 프롬프트를 만든다."""

    display_name = "프롬프트 템플릿"
    category = "llm"
    icon = "layout-template"

    question: Message = port(input=True, display_name="질문")
    context: list[RetrievalHit] = port(input=True, display_name="검색 결과")
    prompt: Message = port(output=True, display_name="프롬프트")

    template: str = param(default=DEFAULT_TEMPLATE, display_name="템플릿", multiline=True)

    def run(self) -> Message:
        if self.question is None:
            raise EngineError("질문 입력이 연결되지 않았습니다.", BAD_INPUT)
        hits = self.context or []
        context_text = "\n\n".join(
            f"[{i + 1}] ({h.provenance.get('doc_title', '?')}"
            f" p.{','.join(map(str, h.provenance.get('pages') or []))}, "
            f"유사도 {h.score:.2f})\n{h.text}"
            for i, h in enumerate(hits)
        ) or "(검색 결과 없음)"
        text = (self.template or DEFAULT_TEMPLATE).replace("{context}", context_text).replace(
            "{question}", self.question.text
        )
        return Message(text=text, history=self.question.history)
