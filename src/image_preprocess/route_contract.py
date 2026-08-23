from __future__ import annotations

from typing import Literal, TypeGuard


RouteName = Literal["balanced", "shadow", "low_contrast", "blurred", "line_art"]

ROUTE_NAMES: tuple[RouteName, ...] = (
    "balanced",
    "shadow",
    "low_contrast",
    "blurred",
    "line_art",
)


def is_route_name(value: object) -> TypeGuard[RouteName]:
    return isinstance(value, str) and value in ROUTE_NAMES
