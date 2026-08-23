"""从已识别 job 收割六笔演示 OCR 缓存。

默认读 CUTOFF_JOB_ROOT 下最新含这些文件名的 job_state.json。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.workflow.demo_ocr_cache import harvest_from_job  # noqa: E402


def _job_paths() -> list[Path]:
    roots: list[Path] = []
    env = (os.getenv("CUTOFF_JOB_ROOT") or "").strip()
    if env:
        roots.append(Path(env))
    roots.append(Path(r"D:\Dev\Temp\cutoff_jobs"))
    roots.append(ROOT / "data" / "jobs")
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(root.glob("*/job_state.json"))
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found


def main() -> int:
    explicit = sys.argv[1] if len(sys.argv) > 1 else ""
    preferred = Path(r"D:\Dev\Temp\cutoff_jobs\f59c99f6850f\job_state.json")
    paths = [Path(explicit)] if explicit else ([preferred] if preferred.is_file() else []) + _job_paths()
    for path in paths:
        if not path.is_file():
            continue
        print(f"读取 {path} …")
        job = json.loads(path.read_text(encoding="utf-8"))
        index = harvest_from_job(job)
        n = int(index.get("count") or 0)
        print(f"写入 {n} 份演示 OCR 缓存")
        for name in sorted((index.get("by_filename") or {}).keys()):
            print(f"  {name}")
        if n:
            return 0
    print("未找到含六笔样例的 job_state.json", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
