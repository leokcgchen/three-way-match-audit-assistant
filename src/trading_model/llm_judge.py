from __future__ import annotations

from typing import Any, Callable, Optional

from .llm_json import live_llm_enabled


def maybe_judge(
    evidence: dict[str, Any],
    *,
    use_llm: bool,
    llm_fn: Optional[Callable[[dict[str, Any]], dict[str, Any]]],
) -> tuple[dict[str, Any], bool, str]:
    if not use_llm:
        return {}, False, "use_llm=False"
    if llm_fn is not None:
        return llm_fn(evidence) or {}, True, ""
    if not live_llm_enabled():
        return {}, False, "live_llm_disabled"
    from .llm_deepseek import call_deepseek, deepseek_available

    if not deepseek_available():
        return {}, False, "no_llm_fn"
    try:
        return call_deepseek(evidence) or {}, True, ""
    except Exception as exc:
        return {}, False, f"deepseek_error:{type(exc).__name__}"
