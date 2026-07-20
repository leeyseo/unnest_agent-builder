from agentsdk import Component, Message, param, port


class BadParserDemo(Component):
    # 위반 ⑤(경고): docstring 없음 — 사이드바 툴팁에 표시할 설명이 없다

    display_name = "불량 파서 데모"
    category = "parsers"

    # 위반 ①: 파서인데 RawFile 입력 포트가 없다 (파일을 못 받음)
    # 위반 ②: 파서인데 출력이 NormalizedDocument가 아니라 Message다
    text: Message = port(input=True, display_name="입력")
    out: Message = port(output=True, display_name="출력")

    # 위반 ③: 비밀값 하드코딩 — 자격증명은 secret_param()/환경변수로만 (원칙 2)
    api_key: str = param(default="sk-proj-FAKE1234567890abcdefgh", display_name="키")

    # 위반 ④: enum default("fast")가 choices에 없다 — 캔버스 폼이 깨진다
    mode: str = param(default="fast", choices=["safe", "strict"], display_name="모드")

    def run(self) -> Message:
        # 위반 ⑥: 설치되지 않은 패키지 임포트 — 실행 시점에야 터질 지뢰
        import superpdf  # noqa: F401

        return Message(text=self.text.text)
