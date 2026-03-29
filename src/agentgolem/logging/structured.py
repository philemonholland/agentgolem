"""Structured logging with JSON file + console output."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog


def setup_logging(
    log_level: str = "INFO",
    data_dir: Path = Path("data"),
    secrets: Any = None,
) -> None:
    """Configure structlog with JSON file + console, wired through redaction."""
    from agentgolem.logging.redaction import RedactionFilter

    # Ensure log directory exists
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build shared processors
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # Add redaction if secrets provided
    if secrets is not None:
        redaction_filter = RedactionFilter(secrets)
        shared_processors.append(redaction_filter.structlog_processor)

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # JSON file handler
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    file_handler = logging.FileHandler(
        log_dir / "activity.jsonl", mode="a", encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)

    # Console handler — only WARNING+ to avoid noise (activity feed handles INFO)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.WARNING)

    # Root logger setup
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Silence noisy third-party loggers on the console
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str = "agentgolem") -> structlog.stdlib.BoundLogger:
    """Get a structlog bound logger."""
    return structlog.get_logger(name)
