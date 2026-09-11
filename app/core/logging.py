import sys
from loguru import logger
from app.core.config import settings

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

def configure_logging() -> None:
    """Set up loguru sinks (stderr, optionally a rotating file)."""
    logger.remove()

    logger.add(
        sys.stderr,
        level="DEBUG" if settings.debug else "INFO",
        format=_LOG_FORMAT,
    )

    if not settings.debug:
        logger.add(
            "logs/app_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="14 days",
            encoding="utf-8",
            level="INFO",
            format=_LOG_FORMAT,
        )