"""Human-in-the-loop 审计日志（JSONL，按日滚动）。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from config.settings import settings


def _log_dir() -> Path:
    path = Path(getattr(settings, "LOGS_DIR", settings.BASE_DIR / "logs"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def hitl_log_path(day: Optional[str] = None) -> Path:
    day = day or datetime.now().strftime("%Y%m%d")
    return _log_dir() / f"hitl_{day}.jsonl"


def current_operator() -> str:
    return (
        os.getenv("HITL_OPERATOR")
        or os.getenv("USERNAME")
        or os.getenv("USER")
        or "local_user"
    )


def append_hitl_event(
    *,
    action: str,
    entity_type: str = "field",
    entity_id: str = "",
    before: Any = None,
    after: Any = None,
    reason: str = "",
    operator: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """追加一条 HITL 事件；失败时仍返回事件对象（不抛给 UI）。"""
    event = {
        "event_id": uuid4().hex[:12],
        "ts": datetime.now().isoformat(timespec="seconds"),
        "operator": operator or current_operator(),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before": before,
        "after": after,
        "reason": reason or "",
        "extra": extra or {},
    }
    try:
        path = hitl_log_path()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        event["persist_error"] = True
    return event


def list_recent_hitl_events(limit: int = 50) -> List[Dict[str, Any]]:
    path = hitl_log_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
