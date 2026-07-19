"""컴포넌트 레지스트리: agentcomponents/ 하위를 임포트 스캔해 스펙 JSON 생성."""
from __future__ import annotations

import importlib
import pkgutil

from .component import Component


class ComponentRegistry:
    def __init__(self) -> None:
        self._components: dict[str, type[Component]] = {}

    def register(self, cls: type[Component]) -> None:
        self._components[cls.__name__] = cls

    def scan_package(self, package_name: str = "agentcomponents") -> None:
        """패키지 하위 모듈 전부 임포트 → Component 서브클래스 자동 등록."""
        pkg = importlib.import_module(package_name)
        for modinfo in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
            importlib.import_module(modinfo.name)
        self._collect(Component)

    def _collect(self, base: type[Component]) -> None:
        for sub in base.__subclasses__():
            # abstract 표시는 상속되지 않게 클래스 자신의 속성만 본다
            if sub.__dict__.get("abstract", False) is not True:
                self.register(sub)
            self._collect(sub)

    def get(self, type_name: str) -> type[Component]:
        if type_name not in self._components:
            raise KeyError(
                f"알 수 없는 컴포넌트 타입 '{type_name}'. "
                f"등록된 타입: {sorted(self._components)}"
            )
        return self._components[type_name]

    def specs(self) -> list[dict]:
        return [cls.spec() for cls in sorted(self._components.values(), key=lambda c: (c.category, c.__name__))]
