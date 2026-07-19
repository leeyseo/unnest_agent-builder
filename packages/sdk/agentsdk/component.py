"""컴포넌트 베이스 클래스 + port()/param()/secret_param() 선언자.

Langflow introspection 패턴: 컴포넌트는 파이썬 클래스, 프론트는 스펙 JSON만 본다.
"""
from __future__ import annotations

import types as _types
import typing
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin


# ---------------------------------------------------------------- 선언 마커


@dataclass
class PortDecl:
    input: bool = False
    output: bool = False
    display_name: str | None = None
    # 아래는 __init_subclass__에서 채움
    name: str = ""
    type_name: str = ""


@dataclass
class ParamDecl:
    default: Any = None
    display_name: str | None = None
    choices: list[str] | None = None
    multiline: bool = False
    secret: bool = False
    required: bool = False
    name: str = ""
    kind: str = "str"  # str | int | float | bool | enum | secret


def port(*, input: bool = False, output: bool = False, display_name: str | None = None) -> Any:
    """엣지가 꽂히는 핸들. 타입 어노테이션이 곧 포트 타입."""
    assert input != output, "port()는 input 또는 output 중 하나여야 합니다"
    return PortDecl(input=input, output=output, display_name=display_name)


def param(
    *,
    default: Any = None,
    display_name: str | None = None,
    choices: list[str] | None = None,
    multiline: bool = False,
    required: bool = False,
) -> Any:
    """캔버스 노드의 파라미터 폼 필드."""
    return ParamDecl(
        default=default,
        display_name=display_name,
        choices=choices,
        multiline=multiline,
        required=required,
    )


def secret_param(*, display_name: str | None = None) -> Any:
    """자격증명 '참조 이름' 파라미터. 값은 백엔드 저장소에만 존재한다 (원칙 2)."""
    return ParamDecl(secret=True, display_name=display_name, kind="secret")


# ---------------------------------------------------------------- 타입명 변환


def type_name(annotation: Any) -> str:
    """어노테이션 → 포트 타입 문자열 ("Message", "list[Chunk]", "Any")."""
    if annotation is Any:
        return "Any"
    origin = get_origin(annotation)
    if origin in (list, typing.List):  # noqa: UP006
        (inner,) = get_args(annotation)
        return f"list[{type_name(inner)}]"
    if isinstance(annotation, (_types.UnionType,)) or get_origin(annotation) is typing.Union:
        # Optional[X] → X 로 취급
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return type_name(args[0])
        return "Any"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def types_compatible(out_type: str, in_type: str) -> bool:
    """출력 타입 == 입력 타입, 또는 어느 한쪽이 Any."""
    return out_type == in_type or out_type == "Any" or in_type == "Any"


# ---------------------------------------------------------------- 실행 컨텍스트


class ExecutionContext:
    """엔진이 컴포넌트에 주입하는 호스트 서비스 접근 창구.

    - run_input: 실행 요청의 입력 (ChatInput/FileInput이 읽음)
    - kb_resolver: kb_id → {"uri","user","password"} (backend=카탈로그, runtime=env)
    - secret_resolver: 자격증명 이름 → 값 (실행 시점에만 해석, 스냅샷 금지)
    """

    def __init__(
        self,
        run_input: dict | None = None,
        kb_resolver: typing.Callable[[str], dict] | None = None,
        secret_resolver: typing.Callable[[str], str] | None = None,
    ) -> None:
        self.run_input = run_input or {}
        self._kb_resolver = kb_resolver
        self._secret_resolver = secret_resolver

    def resolve_kb(self, kb_id: str) -> dict:
        if not self._kb_resolver:
            raise RuntimeError("KB 접속정보 해석기가 설정되지 않았습니다 (엔진 호스트 구성 오류)")
        return self._kb_resolver(kb_id)

    def resolve_secret(self, name: str) -> str:
        if not self._secret_resolver:
            raise RuntimeError("자격증명 해석기가 설정되지 않았습니다 (엔진 호스트 구성 오류)")
        return self._secret_resolver(name)


# ---------------------------------------------------------------- 베이스 클래스


class Component:
    """모든 컴포넌트의 베이스.

    서브클래스는 클래스 속성으로 port()/param()을 선언하고 run()을 구현한다.
    run()은 순수하게 입력 → 출력. 사이드이펙트(DB 쓰기)는 Writer류만.
    """

    display_name: str = ""
    category: str = "misc"
    icon: str = "box"

    # __init_subclass__가 채우는 스펙
    _inputs: dict[str, PortDecl]
    _outputs: dict[str, PortDecl]
    _params: dict[str, ParamDecl]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        hints = typing.get_type_hints(cls)
        inputs: dict[str, PortDecl] = dict(getattr(cls, "_inputs", {}))
        outputs: dict[str, PortDecl] = dict(getattr(cls, "_outputs", {}))
        params: dict[str, ParamDecl] = dict(getattr(cls, "_params", {}))
        for attr, value in list(vars(cls).items()):
            if isinstance(value, PortDecl):
                value.name = attr
                value.type_name = type_name(hints.get(attr, Any))
                (inputs if value.input else outputs)[attr] = value
                delattr(cls, attr)
            elif isinstance(value, ParamDecl):
                value.name = attr
                if not value.secret:
                    ann = hints.get(attr, str)
                    if value.choices:
                        value.kind = "enum"
                    elif ann is int:
                        value.kind = "int"
                    elif ann is float:
                        value.kind = "float"
                    elif ann is bool:
                        value.kind = "bool"
                    else:
                        value.kind = "str"
                params[attr] = value
                delattr(cls, attr)
        cls._inputs = inputs
        cls._outputs = outputs
        cls._params = params

    def __init__(self, *, params: dict | None = None, context: ExecutionContext | None = None):
        self.context = context or ExecutionContext()
        merged = {name: decl.default for name, decl in self._params.items()}
        merged.update(params or {})
        for name, value in merged.items():
            setattr(self, name, value)
        # 입력 포트 버퍼 초기화
        for name in self._inputs:
            setattr(self, name, None)

    # 엔진이 호출: 입력 포트에 값 주입
    def set_input(self, name: str, value: Any) -> None:
        if name not in self._inputs:
            raise KeyError(f"'{type(self).__name__}'에 입력 포트 '{name}'이(가) 없습니다")
        setattr(self, name, value)

    def run(self) -> Any:
        """출력이 1개면 값을, 여러 개면 {포트명: 값} dict를 반환한다."""
        raise NotImplementedError

    # ------------------------------------------------------------ 스펙 JSON

    @classmethod
    def spec(cls) -> dict:
        return {
            "type": cls.__name__,
            "category": cls.category,
            "display_name": cls.display_name or cls.__name__,
            "icon": cls.icon,
            "description": (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else "",
            "inputs": [
                {"name": p.name, "type": p.type_name, "display_name": p.display_name or p.name}
                for p in cls._inputs.values()
            ],
            "outputs": [
                {"name": p.name, "type": p.type_name, "display_name": p.display_name or p.name}
                for p in cls._outputs.values()
            ],
            "params": [
                {
                    "name": p.name,
                    "kind": p.kind,
                    "default": p.default,
                    "display_name": p.display_name or p.name,
                    "choices": p.choices,
                    "multiline": p.multiline,
                    "required": p.required,
                    "secret": p.secret,
                }
                for p in cls._params.values()
            ],
        }
