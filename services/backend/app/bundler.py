"""번들 제조 (CLAUDE.md 10절): 표준 런타임 이미지 + flow JSON + KB 덤프 → 이식 폴더.

설치 = docker load ×2 → .env 작성 → 덤프 적재 → compose up. 폐쇄망에서 네트워크 불필요.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from agentengine import Flow

from . import db
from .config import ROOT
from .provisioner import _docker, get_kb

BUNDLES_DIR = ROOT / "bundles"
RUNTIME_IMAGE = "agent-runtime:latest"
NEO4J_IMAGE = "neo4j:5"


class BundleError(Exception):
    pass


def _sanitize(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip()).strip("-").lower()
    return s or "agent"


def _ensure_runtime_image(client) -> None:
    try:
        client.images.get(RUNTIME_IMAGE)
    except Exception:
        raise BundleError(
            f"런타임 이미지 '{RUNTIME_IMAGE}'가 없습니다. 먼저 빌드하세요:\n"
            "  docker build -f services/runtime/Dockerfile -t agent-runtime ."
        ) from None


def _dump_kb(client, kb: dict, out_dir: Path) -> None:
    """KB 컨테이너를 잠시 멈추고 볼륨에서 neo4j-admin dump를 뜬다."""
    container = client.containers.get(kb["container_name"])
    volume_name = f"kbdata_{kb['kb_id']}"
    was_running = container.status == "running"
    if was_running:
        container.stop(timeout=30)
    try:
        client.containers.run(
            NEO4J_IMAGE,
            command="neo4j-admin database dump neo4j --to-path=/dumps --overwrite-destination=true",
            volumes={
                volume_name: {"bind": "/data", "mode": "rw"},
                str(out_dir): {"bind": "/dumps", "mode": "rw"},
            },
            remove=True,
        )
    finally:
        if was_running:
            container.start()
    if not (out_dir / "neo4j.dump").exists():
        raise BundleError("neo4j.dump가 생성되지 않았습니다 — dump 컨테이너 로그를 확인하세요.")


def _save_image(client, image_tag: str, dest: Path) -> None:
    image = client.images.get(image_tag)
    with open(dest, "wb") as f:
        for chunk in image.save(named=True):
            f.write(chunk)


def _write_compose(bundle_dir: Path, volume_name: str) -> None:
    (bundle_dir / "docker-compose.yml").write_text(
        f"""services:
  kb:
    image: {NEO4J_IMAGE}
    environment:
      - NEO4J_AUTH=neo4j/${{NEO4J_PASSWORD}}
      - NEO4J_server_memory_heap_max__size=1G
    volumes:
      - kbdata:/data
    healthcheck:
      # NEO4J_* env는 neo4j.conf 설정으로 해석되므로 비밀번호는 NEO4J_AUTH에서 파싱한다
      test: ["CMD-SHELL", "cypher-shell -u $${{NEO4J_AUTH%%/*}} -p $${{NEO4J_AUTH#*/}} 'RETURN 1' || exit 1"]
      interval: 10s
      timeout: 10s
      retries: 30
    restart: unless-stopped

  agent:
    image: {RUNTIME_IMAGE}
    depends_on:
      kb:
        condition: service_healthy
    environment:
      - NEO4J_URI=bolt://kb:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=${{NEO4J_PASSWORD}}
      - LLM_BASE_URL=${{LLM_BASE_URL}}
      - LLM_API_KEY=${{LLM_API_KEY}}
      - LLM_MODEL=${{LLM_MODEL}}
      - RUNTIME_API_KEY=${{RUNTIME_API_KEY}}
    volumes:
      - ./flows/agent.json:/flows/agent.json:ro
    ports:
      - "${{AGENT_PORT:-8100}}:8100"
    restart: unless-stopped

volumes:
  kbdata:
    name: {volume_name}
""",
        encoding="utf-8",
    )


def _write_env_example(bundle_dir: Path) -> None:
    (bundle_dir / ".env.example").write_text(
        f"""# 이 파일을 .env 로 복사하고 값을 채우세요.
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-발급받은-키
LLM_MODEL=gpt-4o-mini

# /run 호출 인증 키 (임의 문자열)
RUNTIME_API_KEY={secrets.token_urlsafe(24)}

# KB(Neo4j) 비밀번호 (임의 문자열, 최초 기동 시 설정됨)
NEO4J_PASSWORD={secrets.token_urlsafe(16)}

# 에이전트 포트
AGENT_PORT=8100
""",
        encoding="utf-8",
    )


def _write_install_md(bundle_dir: Path, flow_name: str, volume_name: str) -> None:
    (bundle_dir / "INSTALL.md").write_text(
        f"""# {flow_name} — 설치 안내 (폐쇄망)

요구사항: Docker + docker compose. 인터넷 불필요.

## 자동 설치

- Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`
- Linux:   `bash install.sh`

## 수동 설치 (동일 절차)

```
# 1. 이미지 적재
docker load -i images/agent-runtime.tar
docker load -i images/neo4j.tar

# 2. 환경변수 작성 (LLM 접속정보 입력)
cp .env.example .env   # 편집기로 열어 LLM_* 값 확인/수정

# 3. KB 데이터 적재 (최초 1회, compose up 이전에!)
docker volume create {volume_name}
docker run --rm -v {volume_name}:/data -v "$PWD/data":/dumps neo4j:5 \\
  neo4j-admin database load neo4j --from-path=/dumps --overwrite-destination=true

# 4. 기동
docker compose up -d

# 5. 확인
curl http://localhost:8100/health
curl -X POST http://localhost:8100/run -H "x-api-key: <.env의 RUNTIME_API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{{"input": {{"text": "질문"}}, "stream": false}}'
```

## 무결성 검증

`sha256sum -c SHA256SUMS` (Windows: `Get-FileHash`로 개별 대조)
""",
        encoding="utf-8",
    )


def _write_install_scripts(bundle_dir: Path, volume_name: str) -> None:
    (bundle_dir / "install.ps1").write_text(
        f"""$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
docker load -i images/agent-runtime.tar
docker load -i images/neo4j.tar
if (-not (Test-Path .env)) {{
  Copy-Item .env.example .env
  Write-Host ".env 를 생성했습니다. LLM_* 값을 확인/수정한 뒤 다시 실행하세요." -ForegroundColor Yellow
  exit 1
}}
docker volume create {volume_name}
docker run --rm -v {volume_name}:/data -v "${{PWD}}\\data:/dumps" neo4j:5 neo4j-admin database load neo4j --from-path=/dumps --overwrite-destination=true
docker compose up -d
Write-Host "기동 완료 — http://localhost:8100/health 로 확인하세요."
""",
        encoding="utf-8",
    )
    (bundle_dir / "install.sh").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker load -i images/agent-runtime.tar
docker load -i images/neo4j.tar
if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env 를 생성했습니다. LLM_* 값을 확인/수정한 뒤 다시 실행하세요."
  exit 1
fi
docker volume create {volume_name}
docker run --rm -v {volume_name}:/data -v "$PWD/data":/dumps neo4j:5 \\
  neo4j-admin database load neo4j --from-path=/dumps --overwrite-destination=true
docker compose up -d
echo "기동 완료 — http://localhost:8100/health 로 확인하세요."
""",
        encoding="utf-8",
        newline="\n",
    )


def _write_checksums(bundle_dir: Path) -> None:
    lines = []
    for path in sorted(bundle_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {path.relative_to(bundle_dir).as_posix()}")
    (bundle_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_bundle(flow_id: str) -> dict:
    rows = db.query("SELECT * FROM flows WHERE id = ?", (flow_id,))
    if not rows:
        raise BundleError(f"flow '{flow_id}'가 없습니다")
    flow_dict = json.loads(rows[0]["json"])
    flow = Flow.model_validate(flow_dict)  # 비밀값 오염 검사 포함 (원칙 2)

    kb_ids = {str(n.params["kb_id"]) for n in flow.nodes if n.params.get("kb_id")}
    if len(kb_ids) != 1:
        raise BundleError(
            f"번들은 KB를 정확히 1개 쓰는 flow만 지원합니다 (현재: {sorted(kb_ids) or '없음'})"
        )
    kb_id = kb_ids.pop()
    kb = get_kb(kb_id)

    client = _docker()
    _ensure_runtime_image(client)

    name = _sanitize(flow.name)
    bundle_dir = BUNDLES_DIR / f"{name}-bundle"
    volume_name = f"{name}-kbdata"
    (bundle_dir / "images").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "flows").mkdir(exist_ok=True)
    (bundle_dir / "data").mkdir(exist_ok=True)

    # 1. flow JSON (런타임은 ui 무시하지만 원본 그대로 동봉)
    (bundle_dir / "flows" / "agent.json").write_text(
        json.dumps(flow_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 2. KB 덤프
    _dump_kb(client, kb, bundle_dir / "data")
    # 3. 이미지 저장
    _save_image(client, RUNTIME_IMAGE, bundle_dir / "images" / "agent-runtime.tar")
    _save_image(client, NEO4J_IMAGE, bundle_dir / "images" / "neo4j.tar")
    # 4. compose / env / 문서 / 스크립트 / 체크섬
    _write_compose(bundle_dir, volume_name)
    _write_env_example(bundle_dir)
    _write_install_md(bundle_dir, flow.name, volume_name)
    _write_install_scripts(bundle_dir, volume_name)
    _write_checksums(bundle_dir)

    files = {
        str(p.relative_to(bundle_dir)): p.stat().st_size
        for p in sorted(bundle_dir.rglob("*"))
        if p.is_file()
    }
    return {"path": str(bundle_dir), "kb_id": kb_id, "files": files}
