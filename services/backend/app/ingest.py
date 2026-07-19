"""문서 등록 — 적재도 flow다 (CLAUDE.md 9절). 기본 ingest flow를 보장한다."""
from __future__ import annotations

import json
import uuid

from . import db

DEFAULT_INGEST_FLOW_ID = "ingest-default"

DEFAULT_INGEST_FLOW = {
    "version": "1",
    "name": "기본 문서 적재 (PDF)",
    "nodes": [
        {"id": "n1", "type": "FileInput", "params": {}},
        {"id": "n2", "type": "PDFParser", "params": {"max_pages": 0}},
        {"id": "n3", "type": "SimpleChunker", "params": {"chunk_size": 800, "overlap": 100}},
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


def ensure_default_ingest_flow() -> None:
    if not db.query("SELECT id FROM flows WHERE id = ?", (DEFAULT_INGEST_FLOW_ID,)):
        db.execute(
            "INSERT INTO flows (id, name, json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (DEFAULT_INGEST_FLOW_ID, DEFAULT_INGEST_FLOW["name"],
             json.dumps(DEFAULT_INGEST_FLOW, ensure_ascii=False), db.now(), db.now()),
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
