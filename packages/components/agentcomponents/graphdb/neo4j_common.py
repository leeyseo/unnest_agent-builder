"""Neo4j 접속·KBMeta 대조 공용 헬퍼. 모든 쿼리에 kb_id 필터 (원칙 4)."""
from __future__ import annotations

from agentsdk import ExecutionContext
from agentengine import AUTH_FAILED, UPSTREAM_UNREACHABLE, EngineError

from ..embeddings.local_embedder import current_embed_model


def open_driver(context: ExecutionContext, kb_id: str):
    """카탈로그/env에서 접속정보를 해석해 드라이버를 연다. 호출측이 close 책임."""
    import neo4j
    from neo4j.exceptions import AuthError, ServiceUnavailable

    try:
        conn = context.resolve_kb(kb_id)
    except KeyError as ex:
        raise EngineError(
            f"KB '{kb_id}'가 카탈로그에 없습니다 — 노드의 KB ID를 확인하거나 KB를 먼저 생성하세요.",
            "bad_input",
        ) from ex
    try:
        driver = neo4j.GraphDatabase.driver(
            conn["uri"],
            auth=(conn["user"], conn["password"]),
            notifications_min_severity="OFF",  # 스키마 워밍업 경고가 로그를 덮지 않게
        )
        driver.verify_connectivity()
        return driver
    except AuthError as ex:
        raise EngineError(
            f"KB '{kb_id}' 인증 실패 — 자격증명 저장소의 비밀번호를 확인하세요.",
            AUTH_FAILED,
        ) from ex
    except (ServiceUnavailable, OSError) as ex:
        raise EngineError(
            f"KB '{kb_id}'({conn['uri']})에 연결할 수 없습니다 — 컨테이너 기동 여부를 확인하세요.",
            UPSTREAM_UNREACHABLE,
        ) from ex


def check_kb_meta(driver, kb_id: str) -> None:
    """KBMeta.embed_model과 런타임 EMBED_MODEL이 다르면 즉시 실패 (원칙 5)."""
    with driver.session() as session:
        rec = session.run(
            "MATCH (m:KBMeta {kb_id: $kb_id}) RETURN m.embed_model AS model, m.dim AS dim",
            kb_id=kb_id,
        ).single()
    if rec is None:
        raise EngineError(
            f"KB '{kb_id}'에 KBMeta가 없습니다 — 프로비저너로 생성된 KB인지 확인하세요.",
            UPSTREAM_UNREACHABLE,
        )
    runtime_model = current_embed_model()
    if rec["model"] != runtime_model:
        raise EngineError(
            f"임베딩 모델 불일치: KB '{kb_id}'는 '{rec['model']}'로 적재되었지만 "
            f"런타임 EMBED_MODEL은 '{runtime_model}'입니다. 조용히 망가지는 검색 금지 (원칙 5).",
            "bad_input",
        )
