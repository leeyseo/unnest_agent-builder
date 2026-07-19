"""KB 프로비저너 — KB 생성 = Neo4j 컨테이너 프로비저닝 (CLAUDE.md 8절).

개발 모드(KB_BIND_HOST_PORTS=true)에서는 백엔드가 호스트에서 돌므로
bolt 포트를 127.0.0.1 임의 포트에 개방한다. 컨테이너 배치에서는 agent-net 전용.
"""
from __future__ import annotations

import re
import secrets
import socket
import time

from agentcomponents.embeddings.local_embedder import current_embed_model, embed_dim

from . import credentials, db
from .config import KB_BIND_HOST_PORTS, KB_NETWORK, NEO4J_IMAGE


class ProvisionError(Exception):
    pass


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    if not s:
        raise ProvisionError(f"KB 이름 '{name}'에서 유효한 kb_id를 만들 수 없습니다 (영문/숫자 필요)")
    return s


def _docker():
    import docker

    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as ex:
        raise ProvisionError(
            f"Docker 데몬에 연결할 수 없습니다 — Docker Desktop이 실행 중인지 확인하세요. ({ex})"
        ) from ex


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cred_name(kb_id: str) -> str:
    return f"kb/{kb_id}/neo4j_password"


def kb_conn_info(kb_id: str) -> dict:
    """engine의 kb_resolver — 카탈로그 + 자격증명 저장소에서 접속정보 해석."""
    rows = db.query("SELECT bolt_uri FROM kb WHERE kb_id = ?", (kb_id,))
    if not rows:
        raise KeyError(f"KB '{kb_id}'가 카탈로그에 없습니다. 먼저 KB를 생성하세요.")
    return {
        "uri": rows[0]["bolt_uri"],
        "user": "neo4j",
        "password": credentials.resolve(_cred_name(kb_id)),
    }


def create_kb(name: str) -> dict:
    kb_id = slugify(name)
    if db.query("SELECT kb_id FROM kb WHERE kb_id = ?", (kb_id,)):
        raise ProvisionError(f"KB '{kb_id}'가 이미 존재합니다.")

    client = _docker()
    container_name = "kb-" + kb_id.replace("_", "-")
    password = secrets.token_urlsafe(16)
    credentials.store(_cred_name(kb_id), password)

    # 내부 네트워크 보장
    try:
        client.networks.get(KB_NETWORK)
    except Exception:
        client.networks.create(KB_NETWORK, driver="bridge")

    ports = {}
    host_port = None
    if KB_BIND_HOST_PORTS:
        host_port = _free_port()
        ports["7687/tcp"] = ("127.0.0.1", host_port)

    try:
        client.containers.run(
            NEO4J_IMAGE,
            name=container_name,
            detach=True,
            network=KB_NETWORK,
            environment={
                "NEO4J_AUTH": f"neo4j/{password}",
                "NEO4J_server_memory_heap_max__size": "1G",
            },
            volumes={f"kbdata_{kb_id}": {"bind": "/data", "mode": "rw"}},
            ports=ports,
            restart_policy={"Name": "unless-stopped"},
        )
    except Exception as ex:
        raise ProvisionError(f"Neo4j 컨테이너 기동 실패: {ex}") from ex

    bolt_uri = (
        f"bolt://127.0.0.1:{host_port}" if host_port else f"bolt://{container_name}:7687"
    )
    embed_model = current_embed_model()
    dim = embed_dim(embed_model)

    db.execute(
        "INSERT INTO kb (kb_id, name, container_name, bolt_uri, status, embed_model, dim, created_at) "
        "VALUES (?, ?, ?, ?, 'starting', ?, ?, ?)",
        (kb_id, name, container_name, bolt_uri, embed_model, dim, db.now()),
    )

    try:
        _wait_bolt_ready(bolt_uri, password, timeout_s=180)
        _init_kb_schema(bolt_uri, password, kb_id, embed_model, dim)
    except Exception:
        db.execute("UPDATE kb SET status = 'failed' WHERE kb_id = ?", (kb_id,))
        raise

    db.execute("UPDATE kb SET status = 'ready' WHERE kb_id = ?", (kb_id,))
    return get_kb(kb_id)


def _wait_bolt_ready(bolt_uri: str, password: str, timeout_s: int = 180) -> None:
    import neo4j

    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            driver = neo4j.GraphDatabase.driver(bolt_uri, auth=("neo4j", password))
            driver.verify_connectivity()
            driver.close()
            return
        except Exception as ex:  # 기동 중 접속 거부는 정상
            last_err = ex
            time.sleep(2)
    raise ProvisionError(f"Neo4j가 {timeout_s}s 안에 준비되지 않았습니다: {last_err}")


def _init_kb_schema(bolt_uri: str, password: str, kb_id: str, embed_model: str, dim: int) -> None:
    import neo4j

    driver = neo4j.GraphDatabase.driver(bolt_uri, auth=("neo4j", password))
    try:
        with driver.session() as session:
            session.run(
                f"""
                CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
                FOR (c:Chunk) ON (c.embedding)
                OPTIONS {{indexConfig: {{
                    `vector.dimensions`: {int(dim)},
                    `vector.similarity_function`: 'cosine'
                }}}}
                """
            )
            session.run(
                "MERGE (m:KBMeta {kb_id: $kb_id}) "
                "SET m.embed_model = $embed_model, m.dim = $dim, m.created_at = datetime()",
                kb_id=kb_id, embed_model=embed_model, dim=dim,
            )
    finally:
        driver.close()


def get_kb(kb_id: str) -> dict:
    rows = db.query("SELECT * FROM kb WHERE kb_id = ?", (kb_id,))
    if not rows:
        raise KeyError(f"KB '{kb_id}'가 없습니다")
    return rows[0]


def list_kb() -> list[dict]:
    return db.query("SELECT * FROM kb ORDER BY created_at")


def delete_kb(kb_id: str) -> None:
    kb = get_kb(kb_id)
    client = _docker()
    try:
        container = client.containers.get(kb["container_name"])
        container.remove(force=True)
    except Exception:
        pass  # 컨테이너가 이미 없어도 카탈로그는 정리
    db.execute("DELETE FROM kb WHERE kb_id = ?", (kb_id,))
    db.execute("DELETE FROM documents WHERE kb_id = ?", (kb_id,))
    credentials.delete(_cred_name(kb_id))


def ensure_running(kb_id: str) -> None:
    """백엔드 재시작/재부팅 후 컨테이너가 멈춰 있으면 다시 켠다."""
    kb = get_kb(kb_id)
    client = _docker()
    try:
        container = client.containers.get(kb["container_name"])
        if container.status != "running":
            container.start()
    except Exception as ex:
        raise ProvisionError(f"KB 컨테이너 '{kb['container_name']}'를 찾을 수 없습니다: {ex}") from ex
