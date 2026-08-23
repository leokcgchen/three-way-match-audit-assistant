from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Protocol, Sequence

from src.trading_model.rag.embeddings import embed_texts

# demo 仅用于本机试跑；正式部署必须切到 production（bge-large）。
DEMO_MODEL = "BAAI/bge-small-zh-v1.5"
PRODUCTION_MODEL = "BAAI/bge-large-zh-v1.5"
DEFAULT_BGE = DEMO_MODEL
_MODELS = {
    "bge": DEMO_MODEL,
    "bge-small": DEMO_MODEL,
    "bge-base": "BAAI/bge-base-zh-v1.5",
    "bge-large": PRODUCTION_MODEL,
    "bge-m3": "BAAI/bge-m3",
}
_HF_CACHE = Path(__file__).resolve().parents[1] / "data" / "hf-cache"
_BGE_SINGLETON: Optional["BgeEmbedder"] = None


def resolve_profile() -> str:
    raw = (os.environ.get("CONTRACT_RAG_PROFILE") or "demo").strip().lower()
    if raw in {"prod", "production", "productivity", "deploy"}:
        return "production"
    return "demo"


_DIMS = {
    "char_ngram_v1": 384,
    "hash": 384,
    "test": 384,
    "ngram": 384,
    DEMO_MODEL: 512,
    "BAAI/bge-base-zh-v1.5": 768,
    PRODUCTION_MODEL: 1024,
    "BAAI/bge-m3": 1024,
}


def dimension_for(model_name: str) -> int:
    raw = (model_name or "").strip()
    if raw in _DIMS:
        return _DIMS[raw]
    lowered = raw.lower()
    if lowered in _DIMS:
        return _DIMS[lowered]
    if "bge-small" in lowered:
        return 512
    if "bge-base" in lowered:
        return 768
    if "bge-large" in lowered or "bge-m3" in lowered:
        return 1024
    if lowered in {"hash", "test", "ngram"} or "ngram" in lowered:
        return 384
    raise ValueError(f"unknown embedder dimension for {model_name!r}")


def resolve_embed_model() -> str:
    explicit = (os.environ.get("CONTRACT_RAG_EMBED_MODEL") or "").strip()
    if explicit:
        return explicit
    choice = (os.environ.get("CONTRACT_RAG_EMBEDDER") or "").strip().lower()
    if choice in _MODELS:
        return _MODELS[choice]
    if resolve_profile() == "production":
        return PRODUCTION_MODEL
    return DEMO_MODEL


def _local_model_dir(model_name: str) -> Path:
    return _HF_CACHE / "models" / model_name.replace("/", "--")


def _prepare_hf_env() -> None:
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", "https://hf-mirror.com")
    _HF_CACHE.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(_HF_CACHE))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(_HF_CACHE / "hub"))


def ensure_model_files(model_name: str) -> str:
    """Prefer a local cache; download via ModelScope (China) then HuggingFace mirror."""
    _prepare_hf_env()
    local = _local_model_dir(model_name)
    if (local / "config.json").exists() and (
        (local / "pytorch_model.bin").exists()
        or (local / "model.safetensors").exists()
    ):
        return str(local)
    local.mkdir(parents=True, exist_ok=True)
    try:
        from modelscope.hub.snapshot_download import snapshot_download

        path = snapshot_download(model_name, local_dir=str(local))
        return str(path)
    except Exception:
        return model_name


class Embedder(Protocol):
    name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class HashEmbedder:
    """Deterministic fallback used in tests and before BGE weights are downloaded."""

    name = "char_ngram_v1"
    dim = 384

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return embed_texts(list(texts))


class BgeEmbedder:
    name = DEFAULT_BGE

    def __init__(self, model_name: str | None = None) -> None:
        _prepare_hf_env()
        from sentence_transformers import SentenceTransformer

        self.name = model_name or resolve_embed_model()
        self.dim = dimension_for(self.name)
        local_or_hub = ensure_model_files(self.name)
        self._model = SentenceTransformer(local_or_hub)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in vectors]


def get_embedder() -> Embedder:
    global _BGE_SINGLETON
    choice = (os.environ.get("CONTRACT_RAG_EMBEDDER") or "auto").strip().lower()
    if choice in {"hash", "test", "ngram"}:
        return HashEmbedder()
    model_name = resolve_embed_model()
    if _BGE_SINGLETON is not None and _BGE_SINGLETON.name == model_name:
        return _BGE_SINGLETON
    try:
        _BGE_SINGLETON = BgeEmbedder(model_name)
        return _BGE_SINGLETON
    except Exception:
        return HashEmbedder()


def embedder_name() -> str:
    return get_embedder().name
