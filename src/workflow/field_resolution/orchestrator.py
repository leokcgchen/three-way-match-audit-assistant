"""Persistence-aware orchestration for dynamic field resolution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.workflow.chain_workspace import get_sample
from src.workflow.field_resolution.comparison_plan import (
    build_field_resolution_payload,
    field_resolution_source_hash,
)


def refresh_comparison_plan(
    job_id: str,
    chain_id: str,
    *,
    force: bool = False,
    semantic_payload: Any = None,
) -> dict[str, Any]:
    from src.workflow.job_store import JOB_STORE

    job = JOB_STORE.get(job_id)
    if not job:
        raise KeyError(job_id)
    sample = get_sample(job, chain_id)
    existing = sample.get("field_resolution") if isinstance(sample.get("field_resolution"), dict) else None
    current_hash = field_resolution_source_hash(job, chain_id)
    if existing and not force and existing.get("source_hash") == current_hash and semantic_payload is None:
        cached = deepcopy(existing)
        cached["cache_hit"] = True
        return cached
    resolution = build_field_resolution_payload(job, chain_id, semantic_payload)
    JOB_STORE.set_field_resolution(job_id, chain_id=chain_id, resolution=resolution)
    out = deepcopy(resolution)
    out["cache_hit"] = False
    return out


__all__ = ["refresh_comparison_plan"]
