from __future__ import annotations

from typing import Protocol


class ModelUnavailableError(RuntimeError):
    pass


class ModelGateway(Protocol):
    """Single extension point for optional local or remote model providers."""

    def generate_json(self, task: str, payload: dict, schema: dict) -> dict: ...


class DisabledModelGateway:
    """Offline default. LLM-backed components must always define a fallback."""

    def generate_json(self, task: str, payload: dict, schema: dict) -> dict:
        raise ModelUnavailableError(f"No model provider is configured for task: {task}")
