"""Secret redaction — scrubs sensitive values from all output."""
from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from agentgolem.config.secrets import Secrets


class RedactionFilter:
    """Replaces secret values with a placeholder in strings and dicts."""

    PLACEHOLDER = "[REDACTED]"

    def __init__(self, secrets: Secrets) -> None:
        self._patterns: list[re.Pattern[str]] = []
        for field_name in type(secrets).model_fields:
            value = getattr(secrets, field_name)
            if isinstance(value, SecretStr):
                secret_val = value.get_secret_value()
                if secret_val:
                    self._patterns.append(re.compile(re.escape(secret_val)))

    def redact(self, text: str) -> str:
        """Replace every occurrence of every secret in *text*."""
        for pattern in self._patterns:
            text = pattern.sub(self.PLACEHOLDER, text)
        return text

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Deep-clone *data*, redacting all string values recursively."""
        return self._redact_value(data)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self._redact_value(item) for item in value)
        return value

    # structlog processor interface
    def structlog_processor(
        self, logger: Any, method_name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """Structlog processor that redacts secrets from every event dict."""
        return self.redact_dict(event_dict)
