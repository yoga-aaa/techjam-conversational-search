from __future__ import annotations

from pathlib import Path

from shopping_copilot.core.pipeline import ShoppingCopilotAgent


class Agent:
    """Thin adapter that preserves the official evaluator interface."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        config_path: str | Path | None = None,
    ) -> None:
        self._impl = ShoppingCopilotAgent(catalog_path, config_path=config_path)

    def reset(self, session_id: str, user_profile: dict) -> None:
        self._impl.reset(session_id, user_profile)

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        try:
            return self._impl.respond(
                session_id=session_id,
                user_message=user_message,
                turn=turn,
                top_k=top_k,
            )
        except Exception:
            return {
                "message": "I couldn't process that request; please try again.",
                "ask_attribute": None,
                "recommendations": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }
