"""자격증명 저장소 — Fernet 암호화, 값은 API로 재조회 불가 (원칙 2)."""
from __future__ import annotations

from cryptography.fernet import Fernet

from . import db
from .config import CRED_KEY_PATH


def _fernet() -> Fernet:
    if not CRED_KEY_PATH.exists():
        CRED_KEY_PATH.write_bytes(Fernet.generate_key())
    return Fernet(CRED_KEY_PATH.read_bytes())


def store(name: str, value: str) -> None:
    token = _fernet().encrypt(value.encode())
    db.execute(
        "INSERT OR REPLACE INTO credentials (name, value_encrypted, created_at) VALUES (?, ?, ?)",
        (name, token, db.now()),
    )


def resolve(name: str) -> str:
    rows = db.query("SELECT value_encrypted FROM credentials WHERE name = ?", (name,))
    if not rows:
        raise KeyError(f"자격증명 '{name}'이(가) 등록되어 있지 않습니다. 설정 화면에서 등록하세요.")
    return _fernet().decrypt(rows[0]["value_encrypted"]).decode()


def names() -> list[str]:
    return [r["name"] for r in db.query("SELECT name FROM credentials ORDER BY name")]


def delete(name: str) -> None:
    db.execute("DELETE FROM credentials WHERE name = ?", (name,))
