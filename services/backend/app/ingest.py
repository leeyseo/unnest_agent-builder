"""문서 등록 — 적재도 flow다 (CLAUDE.md 9절).

파일 확장자별 기본 ingest flow를 제공하고, 사용자가 커스텀 ingest flow를
지정하면 그것을 쓴다 (예: 법령 문서용 조문 청커 flow).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import db

DEFAULT_INGEST_FLOW_ID = "ingest-default"


def _pipeline(name: str, parser_type: str, chunker_type: str = "SimpleChunker",
              chunker_params: dict | None = None) -> dict:
    return {
        "version": "1",
        "name": name,
        "nodes": [
            {"id": "n1", "type": "FileInput", "params": {}},
            {"id": "n2", "type": parser_type, "params": {}},
            {"id": "n3", "type": chunker_type,
             "params": chunker_params or {"chunk_size": 800, "overlap": 100}},
            {"id": "n4", "type": "LocalEmbedder", "params": {}},
            {"id": "n5", "type": "Neo4jWriter", "params": {"kb_id": ""}},
        ],
        "edges": [
            {"from": ["n1", "file"], "to": ["n2", "file"]},
            {"from": ["n2", "document"], "to": ["n3", "document"]},
            {"from": ["n3", "chunks"], "to": ["n4", "chunks"]},
            {"from": ["n4", "embedded"], "to": ["n5", "chunks"]},
        ],
        "ui": {"positions": {"n1": [60, 200], "n2": [300, 200], "n3": [540, 200],
                              "n4": [780, 200], "n5": [1020, 200]}},
    }


# flow_id → (flow, 담당 확장자들)
BUILTIN_INGEST_FLOWS: dict[str, tuple[dict, list[str]]] = {
    DEFAULT_INGEST_FLOW_ID: (_pipeline("기본 문서 적재 (PDF)", "PDFParser"), [".pdf"]),
    "ingest-docx": (_pipeline("문서 적재 (DOCX)", "DOCXParser"), [".docx"]),
    "ingest-hwpx": (_pipeline("문서 적재 (HWPX)", "HWPXParser"), [".hwpx"]),
    "ingest-text": (_pipeline("문서 적재 (텍스트)", "TextParser"), [".txt", ".md"]),
    "ingest-law-pdf": (
        _pipeline("법령 적재 (PDF, 조문 청커)", "PDFParser", "ArticleChunker",
                  {"max_chars": 2000}),
        [],  # 확장자 자동 매칭 없음 — 명시적으로 선택해서 사용
    ),
}


def ensure_default_ingest_flow() -> None:
    for flow_id, (flow, _exts) in BUILTIN_INGEST_FLOWS.items():
        if not db.query("SELECT id FROM flows WHERE id = ?", (flow_id,)):
            db.execute(
                "INSERT INTO flows (id, name, json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (flow_id, flow["name"], json.dumps(flow, ensure_ascii=False), db.now(), db.now()),
            )


def flow_id_for_file(filename: str) -> str:
    """확장자에 맞는 기본 ingest flow를 고른다."""
    ext = Path(filename).suffix.lower()
    for flow_id, (_flow, exts) in BUILTIN_INGEST_FLOWS.items():
        if ext in exts:
            return flow_id
    raise ValueError(
        f"'{ext}' 확장자용 기본 적재 flow가 없습니다. "
        f"지원: pdf, docx, hwpx, txt, md — 또는 ingest_flow_id를 직접 지정하세요."
    )


def prepare_ingest_flow(flow_json: dict, kb_id: str) -> dict:
    """ingest flow의 Neo4jWriter 노드에 kb_id를 주입한 사본을 만든다."""
    flow = json.loads(json.dumps(flow_json))
    wrote = False
    for node in flow.get("nodes", []):
        if node["type"] == "Neo4jWriter":
            node.setdefault("params", {})["kb_id"] = kb_id
            wrote = True
    if not wrote:
        raise ValueError("ingest flow에 Neo4jWriter 노드가 없습니다 — 적재 대상 KB를 쓸 수 없습니다.")
    return flow


def register_document(kb_id: str, filename: str, path: str) -> str:
    doc_id = f"d-{uuid.uuid4().hex[:12]}"
    db.execute(
        "INSERT INTO documents (id, kb_id, filename, path, status, created_at) "
        "VALUES (?, ?, ?, ?, 'ingesting', ?)",
        (doc_id, kb_id, filename, path, db.now()),
    )
    return doc_id


def finish_document(doc_id: str, status: str, chunks_written: int) -> None:
    db.execute(
        "UPDATE documents SET status = ?, chunks_written = ? WHERE id = ?",
        (status, chunks_written, doc_id),
    )
    if status == "done":
        rows = db.query("SELECT kb_id FROM documents WHERE id = ?", (doc_id,))
        if rows:
            db.execute(
                "UPDATE kb SET doc_count = (SELECT COUNT(*) FROM documents "
                "WHERE kb_id = ? AND status = 'done') WHERE kb_id = ?",
                (rows[0]["kb_id"], rows[0]["kb_id"]),
            )
