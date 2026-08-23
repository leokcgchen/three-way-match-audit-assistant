"""Deterministic quality sampling for otherwise auto-passed audit chains."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import ceil
from typing import Any


def _stable_number(*parts: str) -> int:
    raw = "|".join(str(part) for part in parts)
    return int(sha256(raw.encode("utf-8")).hexdigest(), 16)


def _sample_count(total: int, rate: float) -> int:
    if total <= 0 or rate <= 0:
        return 0
    return min(total, max(1, ceil(total * min(float(rate), 1.0))))


def _population_rows(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    population = job.get("sample_population")
    rows = population.get("rows") if isinstance(population, dict) else []
    return {
        str(row.get("business_id") or ""): row
        for row in rows or []
        if isinstance(row, dict) and row.get("business_id")
    }


def _days_from_period_end(job: dict[str, Any], row: dict[str, Any]) -> int | None:
    try:
        return abs(
            (date.fromisoformat(str(row.get("book_date") or "")[:10])
             - date.fromisoformat(str(job.get("period_end") or "")[:10])).days
        )
    except ValueError:
        return None


def _eligible_rows(job: dict[str, Any]) -> list[dict[str, Any]]:
    from src.workflow.sample_desk import build_desk_chains

    return [
        row
        for row in build_desk_chains(job)
        if row.get("auto_passed") is True
        and not row.get("event_count")
        and str(row.get("chain_id") or "")
    ]


def _risk_score(
    job: dict[str, Any],
    row: dict[str, Any],
    population_row: dict[str, Any],
    max_amount: float,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    try:
        amount = abs(float(population_row.get("book_amount") or 0))
    except (TypeError, ValueError):
        amount = 0.0
    if amount and max_amount:
        score += amount / max_amount
        if amount == max_amount:
            reasons.append("金额较高")
    days = _days_from_period_end(job, population_row)
    if days is not None and days <= 7:
        score += 1.0 - (days / 8.0)
        reasons.append("接近报告期末")
    doc_count = int(row.get("doc_count") or 0)
    if doc_count >= 4:
        score += min(doc_count / 10.0, 0.8)
        reasons.append("单据关系较复杂")
    return score, reasons


def _selection_plan(
    job: dict[str, Any], *, risk_rate: float, random_rate: float, seed: str
) -> list[dict[str, Any]]:
    eligible = _eligible_rows(job)
    if not eligible:
        return []
    job_id = str(job.get("job_id") or "")
    population = _population_rows(job)
    amounts: list[float] = []
    for item in population.values():
        try:
            amounts.append(abs(float(item.get("book_amount") or 0)))
        except (TypeError, ValueError):
            continue
    max_amount = max(amounts, default=0.0)
    ranked: list[tuple[float, int, dict[str, Any], list[str]]] = []
    for row in eligible:
        chain_id = str(row["chain_id"])
        score, reasons = _risk_score(
            job, row, population.get(chain_id, {}), max_amount
        )
        ranked.append(
            (score, _stable_number(job_id, seed, "risk", chain_id), row, reasons)
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    risk_n = _sample_count(len(eligible), risk_rate)
    selected: dict[str, dict[str, Any]] = {}
    for score, _, row, reasons in ranked[:risk_n]:
        chain_id = str(row["chain_id"])
        selected[chain_id] = {
            "chain_id": chain_id,
            "route": "RISK",
            "risk_score": round(score, 6),
            "risk_reasons": reasons,
        }

    remaining = [row for row in eligible if str(row["chain_id"]) not in selected]
    remaining.sort(
        key=lambda row: _stable_number(job_id, seed, "random", str(row["chain_id"]))
    )
    random_n = min(len(remaining), _sample_count(len(eligible), random_rate))
    for row in remaining[:random_n]:
        chain_id = str(row["chain_id"])
        selected[chain_id] = {
            "chain_id": chain_id,
            "route": "RANDOM",
            "risk_score": 0.0,
            "risk_reasons": [],
        }
    return [selected[key] for key in sorted(selected)]


def select_quality_samples(
    job: dict[str, Any], *, risk_rate: float, random_rate: float, seed: str
) -> list[str]:
    """Return stable chain IDs selected from completed, event-free chains only."""
    return [
        row["chain_id"]
        for row in _selection_plan(
            job, risk_rate=risk_rate, random_rate=random_rate, seed=seed
        )
    ]


def build_quality_sample_selections(
    job: dict[str, Any], *, risk_rate: float, random_rate: float, seed: str
) -> list[dict[str, Any]]:
    job_id = str(job.get("job_id") or "")
    rows: list[dict[str, Any]] = []
    for selected in _selection_plan(
        job, risk_rate=risk_rate, random_rate=random_rate, seed=seed
    ):
        chain_id = str(selected["chain_id"])
        route = str(selected["route"])
        detail = "、".join(selected.get("risk_reasons") or [])
        reason = (
            f"风险抽样：{detail or '综合风险排序靠前'}"
            if route == "RISK"
            else "随机复核自动通过样本"
        )
        rows.append(
            {
                **selected,
                "selection_id": "qs_"
                + sha256(f"{job_id}|{seed}|{chain_id}".encode("utf-8")).hexdigest()[:16],
                "reason": reason,
                "source_ref": f"quality_sample:{seed}:{chain_id}",
                "seed": seed,
            }
        )
    return rows
