"""三单匹配 + 截止性联动 API 路由。"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.hitl_gate import (
    enforce_fields_confirmed_header,
    enforce_matching_confirmed_header,
)
from src.models.schemas import CutoffResponse
from src.three_way_match.matcher import ThreeWayMatcher
from src.three_way_match.models import ThreeWayMatchRequest, ThreeWayMatchResponse

router = APIRouter(
    tags=["three-way-match"],
    dependencies=[
        Depends(enforce_fields_confirmed_header),
        Depends(enforce_matching_confirmed_header),
    ],
)
_matcher = ThreeWayMatcher()


class ThreeWayMatchCombinedResponse(BaseModel):
    """三单匹配与截止性测试合并响应。"""

    match_result: ThreeWayMatchResponse
    cutoff_result: Optional[CutoffResponse] = None
    overall_status: Literal["PASS", "WARNING", "FAIL"]
    cutoff_available: bool = True
    cutoff_skipped_reason: Optional[str] = None
    cutoff_error: Optional[str] = None
    底稿文件路径: Optional[str] = None
    human_readable_summary: str = ""


@router.post(
    "/api/v1/three-way-match",
    response_model=ThreeWayMatchCombinedResponse,
    summary="三单匹配并联动截止性测试",
)
def run_three_way_match(request: ThreeWayMatchRequest) -> dict[str, Any]:
    """接收三单 JSON，执行匹配并联动截止性测试（同进程，避免自调用死锁）。"""
    return _matcher.match_and_cutoff(request, inprocess=True)
