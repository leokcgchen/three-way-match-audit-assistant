"""合同 RAG 向量模型档位。

demo（默认）
    BAAI/bge-small-zh-v1.5  ~100MB
    只给本机试跑、联调、演示。不要当生产检索模型。

production
    BAAI/bge-large-zh-v1.5  ~1.3GB
    正式部署、抽凭生产力必须用这一档。权重从 hf-mirror.com 拉取。

用法：
    python -m trading_model.contract_rag.run_profile
    python -m trading_model.contract_rag.run_profile --profile production
    python -m trading_model.contract_rag.run_profile --profile production --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.trading_model.contract_rag.embedder import (  # noqa: E402
    DEMO_MODEL,
    PRODUCTION_MODEL,
    resolve_embed_model,
    resolve_profile,
)

_POLICY = """
档位约定
  demo         {demo}
               仅 demo / 本机试跑，体积小，召回弱于 large。
  production   {prod}
               正式部署与生产力环境必须切回这一档。
""".format(demo=DEMO_MODEL, prod=PRODUCTION_MODEL)


def apply_profile(profile: str) -> None:
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    os.environ["HUGGINGFACE_HUB_ENDPOINT"] = "https://hf-mirror.com"
    if profile == "production":
        os.environ["CONTRACT_RAG_PROFILE"] = "production"
        os.environ["CONTRACT_RAG_EMBEDDER"] = "bge-large"
        os.environ["CONTRACT_RAG_EMBED_MODEL"] = PRODUCTION_MODEL
    else:
        os.environ["CONTRACT_RAG_PROFILE"] = "demo"
        os.environ["CONTRACT_RAG_EMBEDDER"] = "bge-small"
        os.environ["CONTRACT_RAG_EMBED_MODEL"] = DEMO_MODEL


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Demo 用 bge-small；正式部署必须切回 bge-large。",
    )
    parser.add_argument(
        "--profile",
        choices=("demo", "production"),
        default="demo",
        help="demo=试跑；production=生产力（bge-large）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入当前进程环境（给后续同进程调用用）；默认只打印约定和命令",
    )
    args = parser.parse_args(argv)

    print(_POLICY.strip())
    print()
    apply_profile(args.profile)
    profile = resolve_profile()
    model = resolve_embed_model()
    print(f"当前选择: profile={profile}")
    print(f"当前模型: {model}")
    if profile == "demo":
        print("提醒: 这是 demo 档。上线前请改用 --profile production。")
    else:
        print("提醒: 生产档会下载约 1.3GB 权重，首次请走 hf-mirror。")
    print()
    print("PowerShell 固化本档：")
    if args.profile == "production":
        print('  $env:CONTRACT_RAG_PROFILE="production"')
        print('  $env:CONTRACT_RAG_EMBEDDER="bge-large"')
        print(f'  $env:CONTRACT_RAG_EMBED_MODEL="{PRODUCTION_MODEL}"')
        print('  $env:HF_ENDPOINT="https://hf-mirror.com"')
    else:
        print('  $env:CONTRACT_RAG_PROFILE="demo"')
        print('  $env:CONTRACT_RAG_EMBEDDER="bge-small"')
        print(f'  $env:CONTRACT_RAG_EMBED_MODEL="{DEMO_MODEL}"')
        print('  $env:HF_ENDPOINT="https://hf-mirror.com"')
    if not args.apply:
        print()
        print("未加 --apply：只打印约定，不加载模型权重。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
