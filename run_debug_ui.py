"""启动截止性测试调试控制台（Streamlit）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "src" / "ui" / "debug_console.py"),
        ],
        cwd=str(ROOT),
        check=False,
    )
