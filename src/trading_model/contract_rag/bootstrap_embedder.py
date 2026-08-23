"""Download the demo BGE-small weights via hf-mirror and load them once.

Demo default: BAAI/bge-small-zh-v1.5
Production:   python -m trading_model.contract_rag.run_profile --profile production
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["CONTRACT_RAG_EMBEDDER"] = os.environ.get("CONTRACT_RAG_EMBEDDER") or "bge-small"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from src.trading_model.contract_rag.embedder import get_embedder, resolve_embed_model, resolve_profile

    profile = resolve_profile()
    model = resolve_embed_model()
    print(f"profile={profile}")
    print(f"model={model}")
    print("HF_ENDPOINT=" + os.environ.get("HF_ENDPOINT", ""))
    enc = get_embedder()
    vecs = enc.encode(["控制权转移", "离岸价 FOB Shanghai"])
    print(f"embedder={enc.name} dim={len(vecs[0])} ok")
    if "small" in enc.name:
        print("这是 demo 档。正式部署请: python -m trading_model.contract_rag.run_profile --profile production")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
