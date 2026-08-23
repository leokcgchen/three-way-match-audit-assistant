"""审阅任务 JSON 落盘：与上传 workdir 同目录，重启可续。

环境变量：
  AUDIT_JOB_PERSIST=1（默认）开启；0 关闭
  CUTOFF_JOB_ROOT 同 pipeline.job_workdir
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

STATE_NAME = "job_state.json"
_DEFAULT_JOB_ROOT = "D:/Dev/Temp/cutoff_jobs"


def persist_enabled() -> bool:
    raw = (os.getenv("AUDIT_JOB_PERSIST") or "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    # pytest 默认禁止写入正式任务库；落盘测必须把 CUTOFF_JOB_ROOT 指到临时目录
    if os.environ.get("PYTEST_CURRENT_TEST"):
        root_raw = (os.getenv("CUTOFF_JOB_ROOT") or "").strip()
        if not root_raw:
            return False
        try:
            if Path(root_raw).resolve() == Path(_DEFAULT_JOB_ROOT).resolve():
                return False
        except OSError:
            return False
    return True


def _state_path(job_id: str) -> Path:
    from src.workflow.pipeline import job_workdir

    return job_workdir(job_id) / STATE_NAME


def save_job_state(job: dict[str, Any]) -> Optional[Path]:
    if not persist_enabled():
        return None
    jid = str(job.get("job_id") or "").strip()
    if not jid:
        return None
    path = _state_path(jid)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 运行中标记不阻塞落盘；读取时可忽略
    payload = dict(job)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=0, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_job_state(job_id: str) -> Optional[dict[str, Any]]:
    if not persist_enabled():
        return None
    jid = str(job_id or "").strip()
    if not jid:
        return None
    path = _state_path(jid)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or str(data.get("job_id") or "") != jid:
        return None
    # 重启后不得保留「正在 OCR」假状态
    data["ocr_processing"] = False
    data["ocr_processing_message"] = None
    return data


def list_persisted_job_ids() -> list[str]:
    if not persist_enabled():
        return []
    from src.workflow.pipeline import job_root

    # 扫 CUTOFF_JOB_ROOT 下含 job_state.json 的目录（不要 mkdir 探测目录）
    try:
        root = job_root()
    except Exception:
        return []
    if not root.is_dir():
        return []
    out: list[str] = []
    for child in root.iterdir():
        if child.is_dir() and (child / STATE_NAME).is_file():
            out.append(child.name)
    return out


def delete_job_state(job_id: str) -> None:
    path = _state_path(str(job_id or "").strip())
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
