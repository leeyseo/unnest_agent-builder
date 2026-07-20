"""컴포넌트 계약 검증기 — 새 컴포넌트가 플랫폼 규칙에 맞는지 검사한다.

같은 검증을 세 곳에서 공유한다:
① 개발자 로컬 CLI: uv run python -m agentsdk.validate <파일.py>
② CI: tests/test_component_contract.py 가 전체 컴포넌트에 실행
③ (향후) 업로드 API가 등록 전에 실행

에러 = 계약 위반(등록 불가), 경고 = 권고(등록은 되지만 확인 필요).
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from .component import Component
from .types import TYPE_REGISTRY, Chunk, Message, NormalizedDocument, RawFile, RetrievalHit

# ---------------------------------------------------------------- 리포트

KNOWN_CATEGORIES = {"io", "parsers", "chunkers", "embeddings", "graphdb", "llm", "formatters"}

# 동적(골든) 검사를 건너뛰는 카테고리 — 외부 서비스(DB/LLM)나 모델 로드가 필요하다
DYNAMIC_SKIP_CATEGORIES = {"io", "graphdb", "llm", "embeddings"}


@dataclass
class ValidationReport:
    component: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dynamic: str = "not_run"  # not_run | ok | skipped | failed

    @property
    def ok(self) -> bool:
        return not self.errors and self.dynamic != "failed"


# ---------------------------------------------------------------- 포트 타입 규칙


def _valid_port_type(type_str: str) -> bool:
    """포트 타입은 SDK 타입 계약(TYPE_REGISTRY), Any, list[계약타입]만 허용."""
    if type_str == "Any" or type_str in TYPE_REGISTRY:
        return True
    m = re.fullmatch(r"list\[(\w+)\]", type_str)
    return bool(m and m.group(1) in TYPE_REGISTRY)


def _port_types(ports: dict) -> set[str]:
    return {p.type_name for p in ports.values()}


# 카테고리별 포트 계약: (설명, 검사 함수)
def _contract_parsers(cls: type[Component]) -> list[str]:
    problems = []
    if "RawFile" not in _port_types(cls._inputs):
        problems.append("파서는 RawFile 입력 포트가 필요합니다 (파일을 받아야 함)")
    if "NormalizedDocument" not in _port_types(cls._outputs):
        problems.append("파서는 NormalizedDocument 출력 포트가 필요합니다 (공통 문서 포맷)")
    return problems


def _contract_chunkers(cls: type[Component]) -> list[str]:
    problems = []
    if "NormalizedDocument" not in _port_types(cls._inputs):
        problems.append("청커는 NormalizedDocument 입력 포트가 필요합니다")
    if "list[Chunk]" not in _port_types(cls._outputs):
        problems.append("청커는 list[Chunk] 출력 포트가 필요합니다")
    return problems


def _contract_embeddings(cls: type[Component]) -> list[str]:
    problems = []
    if "list[Chunk]" not in _port_types(cls._inputs):
        problems.append("임베더는 list[Chunk] 입력 포트가 필요합니다")
    if "list[Chunk]" not in _port_types(cls._outputs):
        problems.append("임베더는 list[Chunk] 출력 포트가 필요합니다 (embedding 채워서 반환)")
    return problems


def _contract_graphdb(cls: type[Component]) -> list[str]:
    ins, outs = _port_types(cls._inputs), _port_types(cls._outputs)
    writer = "list[Chunk]" in ins and "IngestReport" in outs
    retriever = "list[RetrievalHit]" in outs
    if not (writer or retriever):
        return [
            "graphdb 컴포넌트는 Writer(list[Chunk]→IngestReport) 또는 "
            "Retriever류(출력 list[RetrievalHit]) 시그니처여야 합니다"
        ]
    return []


def _contract_llm(cls: type[Component]) -> list[str]:
    if "Message" not in _port_types(cls._outputs):
        return ["llm 컴포넌트는 Message 출력 포트가 필요합니다 (다음 노드/채팅으로 흐름)"]
    return []


CATEGORY_CONTRACTS = {
    "parsers": _contract_parsers,
    "chunkers": _contract_chunkers,
    "embeddings": _contract_embeddings,
    "graphdb": _contract_graphdb,
    "llm": _contract_llm,
    # io/formatters는 형태가 다양해 일반 검사만 적용
}

# ---------------------------------------------------------------- 소스 검사

# flow.py의 방어선과 같은 취지 — 컴포넌트 소스에 비밀값이 박혀 있으면 안 된다 (원칙 2)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
]

_KB_ACCESS_PATTERN = re.compile(r"resolve_kb\(|open_driver\(")


def _check_source(cls: type[Component], report: ValidationReport) -> None:
    try:
        source = inspect.getsource(sys.modules[cls.__module__])
    except (OSError, KeyError, TypeError):
        report.warnings.append("소스를 읽을 수 없어 소스 검사를 건너뜁니다")
        return
    for rx in _SECRET_VALUE_PATTERNS:
        if rx.search(source):
            report.errors.append(
                "소스에 비밀값으로 의심되는 리터럴이 있습니다 — 비밀값은 secret_param() "
                "또는 환경변수로 받으세요 (원칙 2)"
            )
            break
    if _KB_ACCESS_PATTERN.search(source) and "kb_id" not in cls._params:
        report.errors.append(
            "KB에 접근(resolve_kb/open_driver)하는데 kb_id 파라미터가 없습니다 — "
            "모든 KB 접근은 kb_id를 받아야 합니다 (원칙 4)"
        )


# ---------------------------------------------------------------- 의존성 검사


@lru_cache(maxsize=1)
def _runtime_dep_closure() -> frozenset[str]:
    """표준 런타임 이미지에 설치되는 배포판 이름 집합 (agentcomponents 의존성 폐포)."""
    seen: set[str] = set()

    def walk(dist_name: str) -> None:
        norm = dist_name.lower().replace("_", "-")
        if norm in seen:
            return
        seen.add(norm)
        try:
            reqs = metadata.requires(dist_name) or []
        except metadata.PackageNotFoundError:
            return
        for r in reqs:
            name = re.split(r"[<>=!~;\[\s(]", r, maxsplit=1)[0].strip()
            if name:
                walk(name)

    for root in ("agentsdk", "agentengine", "agentcomponents", "fastapi", "uvicorn"):
        walk(root)
    return frozenset(seen)


_LOCAL_TOP_LEVEL = {"agentsdk", "agentengine", "agentcomponents"}


def _check_dependencies(cls: type[Component], report: ValidationReport) -> None:
    try:
        source = inspect.getsource(sys.modules[cls.__module__])
        tree = ast.parse(source)
    except (OSError, KeyError, SyntaxError, TypeError):
        return
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            tops.add(node.module.split(".")[0])
    ext = tops - _LOCAL_TOP_LEVEL - set(sys.stdlib_module_names)
    if not ext:
        return
    dist_map = metadata.packages_distributions()
    closure = _runtime_dep_closure()
    for mod in sorted(ext):
        dists = dist_map.get(mod)
        if not dists:
            report.errors.append(
                f"임포트하는 패키지 '{mod}'가 설치되어 있지 않습니다 — "
                "packages/components/pyproject.toml에 의존성을 추가하고 uv sync 하세요"
            )
        elif not any(d.lower().replace("_", "-") in closure for d in dists):
            report.warnings.append(
                f"'{mod}'({', '.join(dists)})는 표준 런타임 이미지 의존성에 없습니다 — "
                "packages/components/pyproject.toml에 추가하고 이미지를 재빌드해야 "
                "번들에서도 동작합니다 (원칙 1)"
            )


# ---------------------------------------------------------------- 정적 검증


def validate_component(cls: type[Component]) -> ValidationReport:
    """컴포넌트 클래스 하나를 정적 검증한다. 에러가 없으면 등록 가능."""
    report = ValidationReport(component=cls.__name__)
    err, warn = report.errors.append, report.warnings.append

    if not (isinstance(cls, type) and issubclass(cls, Component)):
        err("agentsdk.Component를 상속해야 합니다")
        return report

    # 사이드바 렌더 요건
    if not cls.display_name:
        err("display_name이 비어 있습니다 — 사이드바에 표시할 이름이 필요합니다")
    if not (cls.__doc__ or "").strip():
        warn("docstring이 없습니다 — 첫 줄이 사이드바 툴팁에 표시됩니다")
    if not cls.category:
        err("category가 비어 있습니다")
    elif cls.category not in KNOWN_CATEGORIES:
        warn(
            f"category '{cls.category}'는 표준 카테고리({sorted(KNOWN_CATEGORIES)})가 "
            "아닙니다 — 사이드바에 영문 그대로 표시됩니다"
        )

    # run() 구현
    if cls.run is Component.run:
        err("run()을 구현해야 합니다")

    # 포트: 최소 1개 + 타입 계약 준수
    if not cls._inputs and not cls._outputs:
        err("포트가 하나도 없습니다 — port()로 입력/출력을 선언하세요")
    for p in list(cls._inputs.values()) + list(cls._outputs.values()):
        if not _valid_port_type(p.type_name):
            err(
                f"포트 '{p.name}'의 타입 '{p.type_name}'은 타입 계약에 없습니다 — "
                f"허용: {sorted(TYPE_REGISTRY)}, Any, list[계약타입] "
                "(새 타입이 필요하면 agentsdk.types에 추가하고 CLAUDE.md 3절 갱신)"
            )

    # 카테고리별 포트 계약
    contract = CATEGORY_CONTRACTS.get(cls.category)
    if contract:
        report.errors.extend(contract(cls))

    # 스펙 JSON 직렬화 (프론트가 노드를 그릴 수 있어야 함)
    try:
        json.dumps(cls.spec(), ensure_ascii=False)
    except Exception as ex:
        err(f"spec()이 JSON으로 직렬화되지 않습니다: {ex}")

    # enum 파라미터의 default가 choices 안에 있는지
    for p in cls._params.values():
        if p.choices and p.default is not None and p.default not in p.choices:
            err(f"파라미터 '{p.name}'의 default '{p.default}'가 choices {p.choices}에 없습니다")

    _check_source(cls, report)
    _check_dependencies(cls, report)
    return report


# ---------------------------------------------------------------- 동적(골든) 검증


def _sample_doc() -> NormalizedDocument:
    from .types import Block

    return NormalizedDocument(
        doc_type="text",
        source="샘플문서.txt",
        blocks=[
            Block(content="제1조(목적) 이 규정은 검증을 위한 샘플이다. " * 20, meta={"page": 1}),
            Block(content="제2조(정의) 이 규정에서 사용하는 용어의 뜻은 다음과 같다. " * 20, meta={"page": 2}),
        ],
    )


def _sample_value(type_str: str, sample_file: Path | None):
    if type_str in ("Message", "Any"):
        return Message(text="어린이보호구역 주차 과태료는 얼마인가요?")
    if type_str == "NormalizedDocument":
        return _sample_doc()
    if type_str == "list[Chunk]":
        return [Chunk(text="샘플 청크 하나", meta={"seq": 0}), Chunk(text="샘플 청크 둘", meta={"seq": 1})]
    if type_str == "list[RetrievalHit]":
        return [
            RetrievalHit(text="제32조 발췌", score=0.9, provenance={"doc_title": "샘플", "seq": 0}),
            RetrievalHit(text="제33조 발췌", score=0.8, provenance={"doc_title": "샘플", "seq": 1}),
        ]
    if type_str == "RawFile" and sample_file is not None:
        import mimetypes

        mime = mimetypes.guess_type(sample_file.name)[0] or "application/octet-stream"
        return RawFile(path=str(sample_file), mime=mime, filename=sample_file.name)
    return None


def _check_output(value, type_str: str) -> str | None:
    """실행 결과가 선언된 출력 포트 타입과 맞는지. 문제면 메시지 반환."""
    if type_str == "Any":
        return None
    m = re.fullmatch(r"list\[(\w+)\]", type_str)
    if m:
        inner = TYPE_REGISTRY[m.group(1)]
        if not isinstance(value, list):
            return f"출력이 {type_str} 선언인데 list가 아닌 {type(value).__name__}입니다"
        bad = [v for v in value if not isinstance(v, inner)]
        if bad:
            return f"출력 리스트에 {m.group(1)}가 아닌 원소가 있습니다: {type(bad[0]).__name__}"
        return None
    expected = TYPE_REGISTRY.get(type_str)
    if expected and not isinstance(value, expected):
        return f"출력이 {type_str} 선언인데 {type(value).__name__}를 반환했습니다"
    return None


def golden_check(cls: type[Component], sample_file: Path | None = None) -> ValidationReport:
    """샘플 입력으로 run()을 실제 실행해 출력 타입/불변조건을 검증한다."""
    report = ValidationReport(component=cls.__name__)
    if cls.category in DYNAMIC_SKIP_CATEGORIES:
        report.dynamic = "skipped"
        report.warnings.append(
            f"동적 검사 건너뜀 — {cls.category} 카테고리는 외부 서비스/모델이 필요합니다"
        )
        return report

    inputs: dict[str, object] = {}
    for name, p in cls._inputs.items():
        value = _sample_value(p.type_name, sample_file)
        if value is None:
            report.dynamic = "skipped"
            report.warnings.append(
                f"동적 검사 건너뜀 — 입력 '{name}'({p.type_name})의 샘플을 만들 수 없습니다"
                + (" (--sample 파일 지정 필요)" if p.type_name == "RawFile" else "")
            )
            return report
        inputs[name] = value

    try:
        comp = cls(params={})
        for name, value in inputs.items():
            comp.set_input(name, value)
        result = comp.run()
    except Exception as ex:
        # 파서가 bad_input으로 거부한 것은 샘플 형식 불일치(txt 샘플 → PDF 파서 등)일
        # 수 있다 — 실패가 아니라 건너뜀으로 분류한다
        if "RawFile" in _port_types(cls._inputs) and getattr(ex, "kind", None) == "bad_input":
            report.dynamic = "skipped"
            report.warnings.append(
                f"동적 검사 건너뜀 — 샘플이 이 파서의 형식에 맞지 않습니다: {ex}"
            )
            return report
        report.dynamic = "failed"
        report.errors.append(f"샘플 입력 실행 실패 — {type(ex).__name__}: {ex}")
        return report

    outs = list(cls._outputs.values())
    if len(outs) == 1:
        problem = _check_output(result, outs[0].type_name)
        if problem:
            report.dynamic = "failed"
            report.errors.append(problem)
            return report

    # 카테고리 불변조건
    if cls.category == "parsers" and isinstance(result, NormalizedDocument) and not result.blocks:
        report.errors.append("파서 출력의 blocks가 비어 있습니다")
    if cls.category == "chunkers" and isinstance(result, list):
        if not result:
            report.errors.append("청커가 샘플 문서에서 청크를 하나도 만들지 못했습니다")
        elif any(not c.meta for c in result if isinstance(c, Chunk)):
            report.warnings.append(
                "청크 meta가 비어 있습니다 — 출처(provenance)를 meta에 유지해야 인용이 됩니다"
            )

    report.dynamic = "failed" if report.errors else "ok"
    return report


# ---------------------------------------------------------------- CLI


def _load_components_from_file(path: Path) -> list[type[Component]]:
    import importlib.util

    mod_name = f"_validate_target_{path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"'{path}'를 모듈로 로드할 수 없습니다")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return [
        obj
        for obj in vars(module).values()
        if isinstance(obj, type)
        and issubclass(obj, Component)
        and obj is not Component
        and obj.__module__ == mod_name
    ]


def _scan_all() -> list[type[Component]]:
    from .registry import ComponentRegistry

    reg = ComponentRegistry()
    reg.scan_package("agentcomponents")
    return [reg.get(s["type"]) for s in reg.specs()]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m agentsdk.validate",
        description="컴포넌트 계약 검증 — 인자 없이 실행하면 agentcomponents 전체를 검사",
    )
    ap.add_argument("files", nargs="*", help="검사할 컴포넌트 .py 파일 (생략 시 전체)")
    ap.add_argument("--run", action="store_true", help="샘플 입력으로 run()까지 실행 (골든 테스트)")
    ap.add_argument("--sample", type=Path, default=None, help="파서 동적 검사용 샘플 파일")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력 (업로드 API용)")
    args = ap.parse_args(argv)

    try:
        if args.files:
            targets: list[type[Component]] = []
            for f in args.files:
                targets.extend(_load_components_from_file(Path(f)))
            if not targets:
                if args.json:
                    print(json.dumps({"load_error": "Component 서브클래스를 찾지 못했습니다 — "
                                      "agentsdk.Component를 상속한 클래스가 필요합니다"},
                                     ensure_ascii=False))
                else:
                    print("검사할 Component 서브클래스를 찾지 못했습니다")
                return 1
        else:
            targets = _scan_all()
    except Exception as ex:  # 임포트 자체가 실패 (SyntaxError, 상대 임포트 등)
        msg = f"{type(ex).__name__}: {ex}"
        if args.json:
            print(json.dumps({"load_error": msg}, ensure_ascii=False))
        else:
            print(f"파일을 로드할 수 없습니다 — {msg}")
        return 1

    results: list[dict] = []
    failed = 0
    for cls in targets:
        report = validate_component(cls)
        if args.run:
            g = golden_check(cls, sample_file=args.sample)
            report.errors.extend(g.errors)
            report.warnings.extend(g.warnings)
            report.dynamic = g.dynamic
        results.append({
            "component": cls.__name__,
            "category": cls.category,
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
            "dynamic": report.dynamic,
        })
        if not report.ok:
            failed += 1

    if args.json:
        print(json.dumps({"reports": results}, ensure_ascii=False))
        return 1 if failed else 0

    for r in results:
        mark = "✔" if r["ok"] else "✖"
        dyn = {"ok": " [실행 ok]", "skipped": " [실행 건너뜀]", "failed": " [실행 실패]"}.get(
            r["dynamic"], ""
        )
        print(f"{mark} {r['component']} ({r['category']}){dyn}")
        for e in r["errors"]:
            print(f"    에러: {e}")
        for w in r["warnings"]:
            print(f"    경고: {w}")

    total = len(targets)
    print(f"\n{total}개 검사 — 통과 {total - failed}, 실패 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
