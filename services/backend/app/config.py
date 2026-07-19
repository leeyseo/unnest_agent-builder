"""백엔드 설정. 경로는 전부 모노레포 루트 기준."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# services/backend/app/config.py → 모노레포 루트
ROOT = Path(__file__).resolve().parents[3]
# LLM_* 등 비밀값은 루트 .env로 주입 (flow JSON에 저장 금지 — 원칙 2)
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "platform.sqlite3"
CRED_KEY_PATH = DATA_DIR / ".credentials.key"
FLOWS_EXPORT_DIR = ROOT / "flows"

# 개발 모드: 백엔드가 호스트에서 돌므로 KB bolt 포트를 127.0.0.1에 개방한다.
# 컨테이너 배치(운영/제조)에서는 false — agent-net 내부 통신만 (CLAUDE.md 8절).
KB_BIND_HOST_PORTS = os.environ.get("KB_BIND_HOST_PORTS", "true").lower() == "true"
KB_NETWORK = "agent-net"
NEO4J_IMAGE = os.environ.get("NEO4J_IMAGE", "neo4j:5")

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
FLOWS_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
