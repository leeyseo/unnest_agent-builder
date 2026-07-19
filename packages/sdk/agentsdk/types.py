"""엣지 위로 흐르는 데이터 타입 계약 (CLAUDE.md 3절).

포트 타입이 캔버스 연결 가능 여부를 결정한다.
새 타입이 필요하면 여기에 추가하고 CLAUDE.md 3절을 갱신한다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RawFile(BaseModel):
    """업로드된 원본 파일."""

    path: str
    mime: str
    filename: str


class Block(BaseModel):
    """파서가 뽑은 단위 (문단/표/조문 등)."""

    type: str = "text"  # "text" | "table" | "article" | ...
    content: str
    meta: dict = Field(default_factory=dict)  # page, article_no 등


class NormalizedDocument(BaseModel):
    """공통 입력 JSON — 모든 파서의 출력."""

    doc_type: str
    source: str
    blocks: list[Block] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


class Chunk(BaseModel):
    text: str
    meta: dict = Field(default_factory=dict)  # provenance: 출처 블록/조문 참조
    embedding: list[float] | None = None


class RetrievalHit(BaseModel):
    text: str
    score: float
    provenance: dict = Field(default_factory=dict)  # article_no, doc title 등


class Message(BaseModel):
    text: str
    history: list[dict] = Field(default_factory=list)  # 멀티턴은 호출자가 채움 (원칙 3)


class IngestReport(BaseModel):
    kb_id: str
    chunks_written: int
    nodes_created: int


# 포트 타입 문자열 ↔ 실제 타입 매핑 (레지스트리/엔진 공용)
TYPE_REGISTRY: dict[str, type] = {
    "RawFile": RawFile,
    "Block": Block,
    "NormalizedDocument": NormalizedDocument,
    "Chunk": Chunk,
    "RetrievalHit": RetrievalHit,
    "Message": Message,
    "IngestReport": IngestReport,
}
