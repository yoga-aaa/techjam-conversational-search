from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from evaluator.language_stress_driver import (
    DialogueState,
    SurfaceRenderer,
    choose_next_event,
    run_trajectory,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "data" / "templates" / "user_utterance_templates.json"
MODES = ROOT / "configs" / "language_stress_modes.json"


def _variant(template_id: str, utterance: str) -> dict:
    return {
        "template_id": template_id,
        "utterance": utterance,
        "tags": [],
        "author": None,
    }


def _event(
    event_id: str,
    speech_act: str,
    *,
    trigger_attribute: str | None = None,
    values: list[str] | None = None,
    template_id: str,
    utterance: str,
    expected: dict | None = None,
) -> dict:
    values = values or []
    return {
        "event_id": event_id,
        "speech_act": speech_act,
        "trigger_attribute": trigger_attribute,
        "semantic_values": values,
        "expected": expected or {},
        "variants": [_variant(template_id, utterance)],
    }


class FakeAgent:
    def __init__(self, target: str = "TARGET") -> None:
        self.target = target
        self.inputs: list[tuple[str, str, int, int]] = []
        self.turn = 0

    def reset(self, session_id: str, user_profile: dict) -> None:
        self.turn = 0

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        self.inputs.append((session_id, user_message, turn, top_k))
        self.turn = turn
        if turn == 1:
            return {
                "message": "What material do you prefer?",
                "ask_attribute": "material",
                "recommendations": [],
            }
        return {
            "message": "Here are matches.",
            "ask_attribute": None,
            "recommendations": [{"parent_asin": self.target}],
        }


class LanguageStressDriverTest(unittest.TestCase):
    def _session(self, scenario: str = "buying") -> tuple[dict, dict]:
        initial = _event(
            "initial",
            "initial_buying" if scenario == "buying" else "initial_override_preference",
            values=["Test Category", "material: cotton"]
            if scenario == "buying"
            else ["Test Category", "material: leather"],
            template_id="initial_buying_001" if scenario == "buying" else "initial_override_001",
            utterance="initial",
        )
        material = _event(
            "reply:material:1",
            "attribute_reply",
            trigger_attribute="material",
            values=["material: cotton", "material: wool"],
            template_id="attribute_reply_001",
            utterance="material",
        )
        no_pref = _event(
            "no_preference:material",
            "no_preference",
            trigger_attribute="material",
            template_id="no_preference_001",
            utterance="no preference",
        )
        override = _event(
            "override",
            "intent_override",
            values=["material: cotton"],
            template_id="override_002",
            utterance="override",
            expected={"removed_values": ["material: leather"]},
        )
        override["scheduled_turn"] = 3
        session = {
            "sample_id": "sample_1",
            "scenario_type": scenario,
            "initial_event": initial,
            "attribute_replies": {"material": [material], "other": [material]},
            "no_preference_replies": {"material": no_pref},
            "boundary_replies": {},
            "override_event": override if scenario == "intent_override" else None,
            "fallback_event": _event(
                "fallback", "missing_attribute_fallback", template_id="fallback_001", utterance="fallback"
            ),
        }
        sample = {
            "sample_id": "sample_1",
            "scenario_type": scenario,
            "split": "development",
            "fold": 1,
            "ground_truth": {"parent_asin": "TARGET"},
            "user_profile": {"preference_tags": ["material"]},
        }
        return sample, session

    def test_choose_next_event_consumes_batches_and_schedules_override(self) -> None:
        sample, session = self._session()
        state = DialogueState(turn=1)
        response = {"ask_attribute": "material"}
        event, values, diagnostics = choose_next_event(session, response, state)
        self.assertEqual(event["event_id"], "reply:material:1")
        self.assertEqual(values, ["material: cotton", "material: wool"])
        self.assertEqual(diagnostics, [])

        state.turn = 2
        event, values, diagnostics = choose_next_event(session, response, state)
        self.assertEqual(event["event_id"], "no_preference:material")
        self.assertEqual(values, [])
        self.assertIn("attribute_exhausted", diagnostics)

        other_state = DialogueState(turn=1)
        other_state.disclosed_values.add("material: cotton")
        event, values, diagnostics = choose_next_event(
            session, {"ask_attribute": "other"}, other_state
        )
        self.assertEqual(event["event_id"], "reply:material:1")
        self.assertEqual(values, ["material: wool"])

        override_sample, override_session = self._session("intent_override")
        override_state = DialogueState(turn=2)
        event, values, diagnostics = choose_next_event(
            override_session, {"ask_attribute": "material"}, override_state
        )
        self.assertEqual(event["event_id"], "override")
        self.assertEqual(values, ["material: cotton"])
        self.assertTrue(override_state.override_applied)
        self.assertEqual(diagnostics, [])

        pre_override_state = DialogueState(turn=1)
        event, values, diagnostics = choose_next_event(
            override_session, {"ask_attribute": "other"}, pre_override_state
        )
        self.assertEqual(event["event_id"], "reply:material:1")
        # The official simulator does not mark the new override value as
        # disclosed before the override turn, so both values remain available.
        self.assertEqual(values, ["material: cotton", "material: wool"])
        self.assertFalse(pre_override_state.override_applied)

    def test_initial_override_renderer_keeps_old_value(self) -> None:
        _, session = self._session("intent_override")
        renderer = SurfaceRenderer(TEMPLATES, MODES)
        event = session["initial_event"]
        utterance, _, _ = renderer.render(
            mode_name="canonical",
            event=event,
            values=["material: leather"],
            sample_id="sample_1",
            surface_seed="fixture-seed",
        )
        self.assertIn("material: leather", utterance)

    def test_invalid_message_cannot_score_recommendations(self) -> None:
        sample, session = self._session()

        class InvalidAgent(FakeAgent):
            def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
                self.inputs.append((session_id, user_message, turn, top_k))
                return {
                    "message": None,
                    "ask_attribute": None,
                    "recommendations": [{"parent_asin": self.target}],
                }

        trace = run_trajectory(
            sample,
            session,
            SurfaceRenderer(TEMPLATES, MODES),
            InvalidAgent(),
            {"TARGET"},
            mode_name="canonical",
            surface_seed="fixture-seed",
        )
        self.assertFalse(trace["hit"])
        self.assertEqual(trace["turns"][0]["diagnostics"], ["agent_error:invalid_response"])

    def test_run_trajectory_is_mode_rendered_and_stops_on_hit(self) -> None:
        sample, session = self._session()
        renderer = SurfaceRenderer(TEMPLATES, MODES)
        agent = FakeAgent()
        trace = run_trajectory(
            sample,
            session,
            renderer,
            agent,
            {"TARGET"},
            mode_name="canonical",
            surface_seed="fixture-seed",
        )
        self.assertTrue(trace["hit"])
        self.assertEqual(trace["first_hit_turn"], 2)
        self.assertEqual(len(trace["turns"]), 2)
        self.assertEqual(trace["turns"][0]["template_id"], "initial_buying_001")
        self.assertEqual(trace["turns"][1]["template_id"], "attribute_reply_001")
        self.assertTrue(all(len(item) == 4 for item in agent.inputs))
        self.assertNotIn("TARGET", agent.inputs[0][1])

    def test_override_hit_before_override_is_not_counted(self) -> None:
        sample, session = self._session("intent_override")

        class OverrideAgent(FakeAgent):
            def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
                self.inputs.append((session_id, user_message, turn, top_k))
                return {
                    "message": "matches",
                    "ask_attribute": None,
                    "recommendations": (
                        [{"parent_asin": self.target}] if turn <= 2 else []
                    ),
                }

        renderer = SurfaceRenderer(TEMPLATES, MODES)
        trace = run_trajectory(
            sample,
            session,
            renderer,
            OverrideAgent(),
            {"TARGET"},
            mode_name="canonical",
            surface_seed="fixture-seed",
        )
        self.assertFalse(trace["hit"])
        self.assertIsNone(trace["first_hit_turn"])

    def test_mode_config_is_reproducible_for_a_template(self) -> None:
        renderer = SurfaceRenderer(TEMPLATES, MODES)
        event = _event(
            "reply:material:1",
            "attribute_reply",
            trigger_attribute="material",
            values=["material: cotton"],
            template_id="attribute_reply_001",
            utterance="material",
        )
        event["variants"] = [
            _variant("attribute_reply_007", "contextual material"),
            _variant("attribute_reply_008", "another contextual material"),
        ]
        first = renderer.render(
            mode_name="contextual",
            event=event,
            values=["material: cotton"],
            sample_id="sample_1",
            surface_seed="seed",
        )
        second = renderer.render(
            mode_name="contextual",
            event=event,
            values=["material: cotton"],
            sample_id="sample_1",
            surface_seed="seed",
        )
        self.assertEqual(first, second)
        self.assertIn(first[1], {"attribute_reply_007", "attribute_reply_008"})


if __name__ == "__main__":
    unittest.main()
