"""로컬 임베딩 (fastembed/ONNX — torch 불필요, 폐쇄망 이미지에 굽기 쉬움).

EMBED_MODEL 환경변수가 모델을 결정하고, KBMeta와 대조된다 (원칙 5).
"""
from __future__ import annotations

import os

from agentsdk import Chunk, Component, param, port
from agentengine import BAD_INPUT, EngineError

DEFAULT_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model_cache: dict[str, object] = {}


def current_embed_model() -> str:
    return os.environ.get("EMBED_MODEL", DEFAULT_EMBED_MODEL)


def get_embedder(model_name: str | None = None):
    """프로세스 전역 캐시된 fastembed 모델."""
    from fastembed import TextEmbedding

    name = model_name or current_embed_model()
    if name not in _model_cache:
        _model_cache[name] = TextEmbedding(model_name=name)
    return _model_cache[name]


def embed_dim(model_name: str | None = None) -> int:
    from fastembed import TextEmbedding

    name = model_name or current_embed_model()
    for m in TextEmbedding.list_supported_models():
        if m["model"] == name:
            return int(m["dim"])
    raise EngineError(
        f"임베딩 모델 '{name}'은 fastembed가 지원하지 않습니다. "
        "EMBED_MODEL 환경변수를 지원 모델로 설정하세요.",
        BAD_INPUT,
    )


def embed_texts(texts: list[str], model_name: str | None = None) -> list[list[float]]:
    embedder = get_embedder(model_name)
    return [vec.tolist() for vec in embedder.embed(texts)]


class LocalEmbedder(Component):
    """청크에 embedding을 채운다 (EMBED_MODEL, 이미지에 구운 로컬 모델)."""

    display_name = "로컬 임베더"
    category = "embeddings"
    icon = "cpu"

    chunks: list[Chunk] = port(input=True, display_name="청크")
    embedded: list[Chunk] = port(output=True, display_name="임베딩된 청크")

    batch_size: int = param(default=64, display_name="배치 크기")

    def run(self) -> list[Chunk]:
        if not self.chunks:
            raise EngineError("청크 입력이 비어 있습니다.", BAD_INPUT)
        vectors = embed_texts([c.text for c in self.chunks])
        for chunk, vec in zip(self.chunks, vectors):
            chunk.embedding = vec
        return self.chunks
