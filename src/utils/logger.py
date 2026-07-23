import sys
from pathlib import Path

from loguru import logger

LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logger(level: str = "INFO") -> None:
    """配置 loguru：控制台彩色输出 + 按天滚动文件日志。"""
    logger.remove()

    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )

    logger.add(
        LOGS_DIR / "app_{time:YYYY-MM-DD}.log",
        level=level,
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        enqueue=True,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )


setup_logger()

__all__ = ["logger", "setup_logger"]
