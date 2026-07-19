"""OpenAI 호환 LLM 호출. 접속정보는 env(LLM_*) — flow JSON에 비밀값 금지 (원칙 2)."""
from __future__ import annotations

import os

from agentsdk import Component, Message, param, port, secret_param
from agentengine import (
    AUTH_FAILED,
    BAD_INPUT,
    TIMEOUT,
    UPSTREAM_UNREACHABLE,
    EngineError,
)


class OpenAICompatLLM(Component):
    """LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 환경변수로 OpenAI 호환 API를 호출한다."""

    display_name = "LLM (OpenAI 호환)"
    category = "llm"
    icon = "sparkles"

    prompt: Message = port(input=True, display_name="프롬프트")
    answer: Message = port(output=True, display_name="답변")

    temperature: float = param(default=0.2, display_name="온도")
    model: str = param(default="", display_name="모델 (빈값=LLM_MODEL env)")
    api_key_ref: str = secret_param(display_name="API 키 (자격증명)")

    def run(self) -> Message:
        import httpx

        if self.prompt is None:
            raise EngineError("프롬프트 입력이 연결되지 않았습니다.", BAD_INPUT)

        base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        api_key = os.environ.get("LLM_API_KEY", "")
        model = self.model or os.environ.get("LLM_MODEL", "")
        # 캔버스에서 자격증명 이름을 선택했다면 그것이 env보다 우선
        if self.api_key_ref:
            api_key = self.context.resolve_secret(self.api_key_ref)
        if not base_url:
            raise EngineError(
                "LLM_BASE_URL이 설정되지 않았습니다. .env에 LLM_BASE_URL/LLM_API_KEY/"
                "LLM_MODEL을 넣거나 백엔드 환경변수로 지정하세요.",
                BAD_INPUT,
            )
        if not model:
            raise EngineError("LLM_MODEL이 설정되지 않았습니다.", BAD_INPUT)

        messages = [
            *self.prompt.history,
            {"role": "user", "content": self.prompt.text},
        ]
        try:
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": float(self.temperature),
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        except httpx.ConnectTimeout as ex:
            raise EngineError(
                f"{base_url} 응답 없음 (connect timeout 10s)", UPSTREAM_UNREACHABLE
            ) from ex
        except httpx.TimeoutException as ex:
            raise EngineError(f"{base_url} 응답 지연 (timeout 60s)", TIMEOUT) from ex
        except httpx.HTTPError as ex:
            raise EngineError(f"{base_url} 연결 실패: {ex}", UPSTREAM_UNREACHABLE) from ex

        if resp.status_code in (401, 403):
            raise EngineError(
                "LLM API 키 인증 실패 — 자격증명(LLM_API_KEY)을 확인하세요.", AUTH_FAILED
            )
        if resp.status_code >= 400:
            raise EngineError(
                f"LLM API 오류 {resp.status_code}: {resp.text[:300]}", UPSTREAM_UNREACHABLE
            )
        data = resp.json()
        try:
            answer_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as ex:
            raise EngineError(
                f"LLM 응답 형식이 OpenAI 호환이 아닙니다: {str(data)[:300]}",
                UPSTREAM_UNREACHABLE,
            ) from ex
        return Message(text=answer_text, history=self.prompt.history)
