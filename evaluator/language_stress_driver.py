"""Drive V2 response banks through the Agent for paired language stress tests."""

from __future__ import annotations

import copy
import json
import sys
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    metric_summary,
    normalize_recommendations,
)
from starter.agent import Agent

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluator.v2_language_support import (  # noqa: E402
    ATTRIBUTE_ORDER,
    TemplateCatalog,
    attribute_label as _attribute_label,
    join_values as _join_values,
    normalize_text as _normalize_text,
    sha256_file as _sha256_file,
    stable_hash as _stable_hash,
)


DRIVER_VERSION = "1.1.0"
ALLOWED_ATTRIBUTES = set(ATTRIBUTE_ORDER) - {"category"}
DEFAULT_MODES = ("canonical", "explicit", "conversational", "contextual")

_SPEECH_ACT_GROUP = {
    "initial_buying": "initial_buying",
    "initial_browsing": "initial_browsing",
    "initial_override_preference": "initial_override",
    "attribute_reply": "attribute_reply",
    "no_preference": "no_preference",
    "boundary_no_preference": "boundary",
    "intent_override": "override",
    "missing_attribute_fallback": "fallback",
}


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("%s:%d must be a JSON object" % (path, line_number))
            rows.append(value)
    return rows


def _load_modes(path: str | Path) -> dict[str, dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != 1:
        raise ValueError("language mode config schema_version must be 1")
    modes = payload.get("modes")
    if not isinstance(modes, dict) or not modes:
        raise ValueError("language mode config must contain modes")
    result: dict[str, dict[str, Any]] = {}
    for mode_name, mode in modes.items():
        if not isinstance(mode_name, str) or not mode_name.strip():
            raise ValueError("language mode names must be non-empty strings")
        if not isinstance(mode, dict) or not isinstance(mode.get("groups"), dict):
            raise ValueError("mode %s must contain a groups object" % mode_name)
        groups = mode["groups"]
        missing = set(_SPEECH_ACT_GROUP.values()) - set(groups)
        if missing:
            raise ValueError("mode %s is missing groups: %s" % (mode_name, sorted(missing)))
        normalized_groups: dict[str, list[str]] = {}
        for group_name, template_ids in groups.items():
            if not isinstance(template_ids, list) or not template_ids:
                raise ValueError("mode %s group %s must be a non-empty list" % (mode_name, group_name))
            if any(not isinstance(template_id, str) or not template_id for template_id in template_ids):
                raise ValueError("mode %s group %s has invalid template IDs" % (mode_name, group_name))
            normalized_groups[group_name] = list(dict.fromkeys(template_ids))
        result[mode_name] = {
            "description": str(mode.get("description") or ""),
            "groups": normalized_groups,
        }
    return result


def _event_group(event: dict[str, Any]) -> str:
    speech_act = str(event.get("speech_act") or "")
    try:
        return _SPEECH_ACT_GROUP[speech_act]
    except KeyError as exc:
        raise ValueError("unsupported response-bank speech_act: %s" % speech_act) from exc


def _value_key(value: object) -> str:
    return _normalize_text(value).casefold()


def _event_values(
    event: dict[str, Any],
    state: "DialogueState",
    *,
    blocked_values: set[str] | None = None,
) -> list[str]:
    """Return still-available values, allowing partially consumed batches."""

    blocked = state.disclosed_values | state.removed_values | (blocked_values or set())
    return [value for value in event.get("semantic_values") or [] if _value_key(value) not in blocked]


def _copy_expected_for_values(event: dict[str, Any], values: list[str]) -> dict[str, Any]:
    expected = copy.deepcopy(event.get("expected") or {})
    if str(event.get("speech_act") or "") == "attribute_reply":
        attribute = str(event.get("trigger_attribute") or "other")
        if values:
            expected["slot_values"] = {attribute: values}
        else:
            expected["slot_values"] = {}
    return expected


@dataclass
class DialogueState:
    turn: int = 0
    disclosed_event_ids: set[str] = field(default_factory=set)
    disclosed_values: set[str] = field(default_factory=set)
    removed_values: set[str] = field(default_factory=set)
    next_batch_by_attribute: dict[str, int] = field(default_factory=dict)
    no_preference_attributes: set[str] = field(default_factory=set)
    boundary_used: bool = False
    override_applied: bool = False
    last_ask_attribute: str | None = None
    diagnostics: Counter[str] = field(default_factory=Counter)

    def mark_event(self, event: dict[str, Any], values: Iterable[str] = ()) -> None:
        event_id = str(event.get("event_id") or "")
        if event_id:
            self.disclosed_event_ids.add(event_id)
        speech_act = str(event.get("speech_act") or "")
        for value in values:
            normalized = _value_key(value)
            if normalized:
                self.disclosed_values.add(normalized)
        if speech_act == "boundary_no_preference":
            self.boundary_used = True
            attribute = str(event.get("trigger_attribute") or "")
            if attribute:
                self.no_preference_attributes.add(attribute)
        elif speech_act == "no_preference":
            attribute = str(event.get("trigger_attribute") or "")
            if attribute:
                self.no_preference_attributes.add(attribute)
        elif speech_act == "intent_override":
            self.override_applied = True
            expected = event.get("expected") or {}
            for value in expected.get("removed_values") or []:
                normalized = _value_key(value)
                if normalized:
                    self.removed_values.add(normalized)


class SurfaceRenderer:
    """Select and render a configured template without changing event semantics."""

    def __init__(self, templates_path: str | Path, modes_path: str | Path) -> None:
        self.templates = TemplateCatalog.load(templates_path)
        self.modes = _load_modes(modes_path)
        self.template_text: dict[str, str] = {}
        self.template_tags: dict[str, list[str]] = {}
        for entries in self.templates.groups.values():
            for entry in entries:
                self.template_text[entry["template_id"]] = entry["template"]
                self.template_tags[entry["template_id"]] = list(entry["tags"])
        all_ids = set(self.template_text)
        for mode_name, mode in self.modes.items():
            for group_name, template_ids in mode["groups"].items():
                unknown = set(template_ids) - all_ids
                if unknown:
                    raise ValueError(
                        "mode %s group %s references unknown templates: %s"
                        % (mode_name, group_name, sorted(unknown))
                    )

    def choose_template(
        self,
        *,
        mode_name: str,
        event: dict[str, Any],
        sample_id: str,
        surface_seed: str,
    ) -> str:
        try:
            configured = self.modes[mode_name]["groups"][_event_group(event)]
        except KeyError as exc:
            raise ValueError("mode %s cannot render event" % mode_name) from exc
        available = {
            str(variant.get("template_id")): variant
            for variant in event.get("variants") or []
        }
        candidates = [template_id for template_id in configured if template_id in available]
        if not candidates:
            raise ValueError(
                "mode %s has no available template for event %s"
                % (mode_name, event.get("event_id"))
            )
        index = _stable_hash(surface_seed, sample_id, mode_name, event.get("event_id")) % len(candidates)
        return candidates[index]

    def render(
        self,
        *,
        mode_name: str,
        event: dict[str, Any],
        values: list[str],
        sample_id: str,
        surface_seed: str,
    ) -> tuple[str, str, list[str]]:
        template_id = self.choose_template(
            mode_name=mode_name,
            event=event,
            sample_id=sample_id,
            surface_seed=surface_seed,
        )
        speech_act = str(event.get("speech_act") or "")
        attribute = str(event.get("trigger_attribute") or "other")
        semantic_values = values or list(event.get("semantic_values") or [])
        format_values = {
            "category": str((event.get("semantic_values") or [""])[0]),
            "value": _join_values(semantic_values),
            "values": _join_values(semantic_values),
            "attribute_label": _attribute_label(attribute),
            "old_value": "",
            "new_value": "",
        }
        if speech_act == "initial_override_preference":
            # _initial_values() passes only the old preference to the renderer.
            # Slicing it again used to render every override opener as an empty
            # sentence ("my preference is: .").
            format_values["old_value"] = _join_values(values or semantic_values[1:])
        elif speech_act == "intent_override":
            expected = event.get("expected") or {}
            removed = expected.get("removed_values") or []
            format_values["old_value"] = _join_values([str(value) for value in removed])
            format_values["new_value"] = _join_values(semantic_values)
        if speech_act in {"no_preference", "boundary_no_preference", "missing_attribute_fallback"}:
            format_values["values"] = ""
        try:
            text = _normalize_text(self.template_text[template_id].format_map(format_values))
        except KeyError as exc:
            raise ValueError("template %s has unsupported placeholder %s" % (template_id, exc.args[0])) from exc
        if not text:
            raise ValueError("template %s rendered empty text" % template_id)
        return text, template_id, self.template_tags[template_id]


def _next_attribute_event(
    session: dict[str, Any],
    attribute: str,
    state: DialogueState,
) -> tuple[dict[str, Any], list[str]] | None:
    events = (session.get("attribute_replies") or {}).get(attribute) or []
    index = state.next_batch_by_attribute.get(attribute, 0)
    while index < len(events):
        event = events[index]
        state.next_batch_by_attribute[attribute] = index + 1
        index += 1
        values = _event_values(event, state)
        if values:
            return event, values
    return None


def choose_next_event(
    session: dict[str, Any],
    response: dict[str, Any],
    state: DialogueState,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Select the next semantic user event from the Agent's structured question."""

    diagnostics: list[str] = []
    scenario = str(session.get("scenario_type") or "")
    next_turn = state.turn + 1
    if scenario == "intent_override" and not state.override_applied:
        override_event = session.get("override_event")
        scheduled = int((override_event or {}).get("scheduled_turn", 0)) if override_event else 0
        if override_event and next_turn == scheduled:
            state.mark_event(override_event, override_event.get("semantic_values") or [])
            return override_event, list(override_event.get("semantic_values") or []), diagnostics

    raw_attribute = response.get("ask_attribute")
    # The official contract uses exact allowed strings.  Do not silently make
    # invalid values such as "Material" or " material " valid here.
    if not isinstance(raw_attribute, str) or not raw_attribute:
        state.diagnostics["fallback_count"] += 1
        diagnostics.append("missing_ask_attribute")
        event = session["fallback_event"]
        state.mark_event(event)
        return event, [], diagnostics

    previous_attribute = state.last_ask_attribute
    attribute = raw_attribute
    state.last_ask_attribute = attribute
    if attribute not in ALLOWED_ATTRIBUTES:
        state.diagnostics["invalid_ask_attribute"] += 1
        diagnostics.append("invalid_ask_attribute")
        attribute = "other"

    if scenario == "boundary" and not state.boundary_used:
        event = (session.get("boundary_replies") or {}).get(attribute)
        if event:
            state.diagnostics["boundary_count"] += 1
            state.mark_event(event)
            return event, [], diagnostics

    selection = _next_attribute_event(session, attribute, state)
    if selection:
        event, values = selection
        state.mark_event(event, values)
        return event, values, diagnostics

    state.diagnostics["no_preference_count"] += 1
    if previous_attribute == attribute:
        state.diagnostics["exhausted_attribute_count"] += 1
        diagnostics.append("attribute_exhausted")
    event = (session.get("no_preference_replies") or {}).get(attribute)
    if not event:
        event = session["fallback_event"]
        state.mark_event(event)
        return event, [], diagnostics
    state.mark_event(event)
    return event, [], diagnostics


def _initial_values(event: dict[str, Any]) -> list[str]:
    speech_act = str(event.get("speech_act") or "")
    semantic = list(event.get("semantic_values") or [])
    if speech_act in {"initial_buying", "initial_override_preference"}:
        return semantic[1:]
    return []


def _initial_event_for_session(session: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    event = session.get("initial_event")
    if not isinstance(event, dict):
        raise ValueError("session %s has no initial_event" % session.get("sample_id"))
    return event, _initial_values(event)


def _safe_agent_response(agent: Any, session_id: str, message: str, turn: int, top_k: int) -> dict[str, Any]:
    try:
        response = agent.respond(session_id, message, turn, top_k)
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        return {
            "message": "",
            "ask_attribute": None,
            "recommendations": [],
            "_driver_error": type(exc).__name__,
        }
    # The official evaluator treats a non-dict response or a response without
    # a string `message` as an invalid empty response.  In particular, do not
    # score recommendations from `{message: None, recommendations: [...]}`.
    if not isinstance(response, dict) or not isinstance(response.get("message"), str):
        return {"message": "", "ask_attribute": None, "recommendations": [], "_driver_error": "invalid_response"}
    return response


def run_trajectory(
    sample: dict[str, Any],
    session: dict[str, Any],
    renderer: SurfaceRenderer,
    agent: Any,
    catalog_ids: set[str],
    *,
    mode_name: str,
    surface_seed: str,
    max_turns: int = MAX_TURNS,
    top_k: int = TOP_K,
) -> dict[str, Any]:
    sample_id = str(sample["sample_id"])
    target = str(sample["ground_truth"]["parent_asin"])
    # Official evaluation creates an opaque random session id.  Keeping mode or
    # sample_id in this value lets an Agent branch on the test condition.
    session_id = "language_stress:%s" % uuid.uuid4().hex
    agent.reset(session_id, sample.get("user_profile") or {})
    state = DialogueState()
    event, values = _initial_event_for_session(session)
    # The official simulator shows an old preference in an override opener but
    # does not add that value to its disclosed set until the subsequent policy
    # reveals it.  Preserve that distinction in the stress driver.
    state.mark_event(
        event,
        [] if str(event.get("speech_act") or "") == "initial_override_preference" else values,
    )

    turns: list[dict[str, Any]] = []
    first_hit_turn: int | None = None
    best_rank: int | None = None
    for turn in range(1, max_turns + 1):
        state.turn = turn
        message, template_id, template_tags = renderer.render(
            mode_name=mode_name,
            event=event,
            values=values,
            sample_id=sample_id,
            surface_seed=surface_seed,
        )
        started = time.perf_counter()
        response = _safe_agent_response(agent, session_id, message, turn, top_k)
        latency_ms = (time.perf_counter() - started) * 1000.0
        recommendations = normalize_recommendations(response.get("recommendations"), catalog_ids)[
            : min(top_k, TOP_K)
        ]
        rank = recommendations.index(target) + 1 if target in recommendations else None
        eligible = str(sample.get("scenario_type") or "") != "intent_override" or state.override_applied
        if eligible and rank is not None:
            first_hit_turn = turn
            best_rank = rank
        diagnostics: list[str] = []
        if response.get("_driver_error"):
            diagnostics.append("agent_error:%s" % response["_driver_error"])
        if (
            "ask_attribute" in response
            and response.get("ask_attribute") is not None
            and not isinstance(response.get("ask_attribute"), str)
        ):
            diagnostics.append("ask_attribute_not_string")
        turns.append({
            "turn": turn,
            "event_id": event.get("event_id"),
            "speech_act": event.get("speech_act"),
            "template_id": template_id,
            "template_tags": template_tags,
            "utterance": message,
            "expected": _copy_expected_for_values(event, values),
            "agent_message": str(response.get("message") or ""),
            "ask_attribute": response.get("ask_attribute"),
            "usage": copy.deepcopy(response.get("usage")) if isinstance(response.get("usage"), dict) else {},
            "latency_ms": round(latency_ms, 3),
            "recommendations": recommendations,
            "target_rank": rank,
            "override_applied": state.override_applied,
            "diagnostics": diagnostics,
        })
        if first_hit_turn is not None:
            break
        if turn == max_turns:
            break
        event, values, next_diagnostics = choose_next_event(session, response, state)
        if next_diagnostics:
            turns[-1]["next_event_diagnostics"] = next_diagnostics

    hit = first_hit_turn is not None
    return {
        "sample_id": sample_id,
        "mode": mode_name,
        "scenario_type": sample.get("scenario_type"),
        "split": sample.get("split"),
        "fold": sample.get("fold"),
        "hit": hit,
        "first_hit_turn": first_hit_turn,
        "best_rank": best_rank,
        "reciprocal_rank": 0.0 if best_rank is None else 1.0 / best_rank,
        "turns": turns,
    }


def _metric_summary(rows: Iterable[dict[str, Any]], *, max_turns: int = MAX_TURNS) -> dict[str, Any]:
    rows = list(rows)
    if max_turns == MAX_TURNS:
        return metric_summary(rows)
    if not rows:
        return {"sample_count": 0, "hit_rate_at_10": 0.0, "mrr": 0.0, "mttc": None}
    hit_rate = sum(int(item["hit"]) for item in rows) / len(rows)
    mrr = sum(float(item["reciprocal_rank"]) for item in rows) / len(rows)
    mttc = sum(
        item["first_hit_turn"] if item["first_hit_turn"] is not None else max_turns + 1
        for item in rows
    ) / len(rows)
    return {
        "sample_count": len(rows),
        "hit_rate_at_10": round(hit_rate, 6),
        "mrr": round(mrr, 6),
        "mttc": round(mttc, 6),
    }


def _score_metrics(metrics: dict[str, Any], *, max_turns: int = MAX_TURNS) -> dict[str, Any]:
    if metrics.get("mttc") is None:
        return {**metrics, "efficiency": None, "recommended_technical_score": None}
    miss_turn = max_turns + 1.0
    efficiency = max(0.0, min(1.0, (miss_turn - float(metrics["mttc"])) / max_turns))
    score = (
        0.50 * float(metrics["hit_rate_at_10"])
        + 0.30 * float(metrics["mrr"])
        + 0.20 * efficiency
    )
    return {
        **metrics,
        "efficiency": round(efficiency, 6),
        "recommended_technical_score": round(score, 6),
    }


def _group_metrics(rows: Iterable[dict[str, Any]], *, max_turns: int = MAX_TURNS) -> dict[str, Any]:
    return _score_metrics(_metric_summary(rows, max_turns=max_turns), max_turns=max_turns)


def _usage_summary(traces: Iterable[dict[str, Any]]) -> dict[str, int]:
    prompt_tokens = 0
    completion_tokens = 0
    for trace in traces:
        for turn in trace.get("turns") or []:
            usage = turn.get("usage") or {}
            if isinstance(usage, dict):
                prompt = usage.get("prompt_tokens")
                completion = usage.get("completion_tokens")
                if isinstance(prompt, int) and prompt >= 0:
                    prompt_tokens += prompt
                if isinstance(completion, int) and completion >= 0:
                    completion_tokens += completion
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _latency_summary(traces: Iterable[dict[str, Any]]) -> dict[str, float | int | None]:
    values = [
        float(turn["latency_ms"])
        for trace in traces
        for turn in trace.get("turns") or []
        if isinstance(turn.get("latency_ms"), (int, float))
    ]
    if not values:
        return {"call_count": 0, "mean_ms": None, "p95_ms": None, "total_ms": 0.0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {
        "call_count": len(values),
        "mean_ms": round(statistics.fmean(values), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "total_ms": round(sum(values), 3),
    }


def _path_signature(trace: dict[str, Any]) -> list[tuple[object, object]]:
    return [(turn.get("ask_attribute"), turn.get("event_id")) for turn in trace.get("turns") or []]


def _paired_summary(
    traces_by_sample: dict[str, dict[str, dict[str, Any]]],
    modes: list[str],
    *,
    max_turns: int = MAX_TURNS,
) -> dict[str, Any]:
    sample_ids = sorted(traces_by_sample)
    canonical = "canonical" if "canonical" in modes else modes[0]
    all_success = 0
    mode_details: dict[str, dict[str, Any]] = {}
    for mode in modes:
        mode_rows = [traces_by_sample[sample_id][mode] for sample_id in sample_ids]
        mode_details[mode] = {
            "success_count": sum(int(row["hit"]) for row in mode_rows),
            "failure_after_canonical_success": 0,
            "success_after_canonical_failure": 0,
            "mean_first_hit_turn_delta_vs_canonical": None,
            "first_path_divergence_count": 0,
        }
    deltas: dict[str, list[float]] = defaultdict(list)
    censored_deltas: dict[str, list[float]] = defaultdict(list)
    for sample_id in sample_ids:
        rows = traces_by_sample[sample_id]
        if all(rows[mode]["hit"] for mode in modes):
            all_success += 1
        base = rows[canonical]
        base_path = _path_signature(base)
        for mode in modes:
            if mode == canonical:
                continue
            row = rows[mode]
            if base["hit"] and not row["hit"]:
                mode_details[mode]["failure_after_canonical_success"] += 1
            if not base["hit"] and row["hit"]:
                mode_details[mode]["success_after_canonical_failure"] += 1
            if base["first_hit_turn"] is not None and row["first_hit_turn"] is not None:
                deltas[mode].append(row["first_hit_turn"] - base["first_hit_turn"])
            base_turn = base["first_hit_turn"] or max_turns + 1
            row_turn = row["first_hit_turn"] or max_turns + 1
            censored_deltas[mode].append(row_turn - base_turn)
            variant_path = _path_signature(row)
            shared_turns = min(len(base_path), len(variant_path))
            first_divergence_turn = next(
                (
                    turn_index + 1
                    for turn_index in range(shared_turns)
                    if base_path[turn_index] != variant_path[turn_index]
                ),
                None,
            )
            if first_divergence_turn is not None:
                mode_details[mode]["first_path_divergence_count"] += 1
                mode_details[mode].setdefault("first_path_divergence_turns", Counter())[
                    str(first_divergence_turn)
                ] += 1
    for mode in modes:
        if deltas[mode]:
            mode_details[mode]["mean_first_hit_turn_delta_vs_canonical"] = round(
                sum(deltas[mode]) / len(deltas[mode]), 6
            )
        mode_details[mode]["mean_censored_first_hit_turn_delta_vs_canonical"] = (
            round(sum(censored_deltas[mode]) / len(censored_deltas[mode]), 6)
            if censored_deltas[mode]
            else None
        )
        if isinstance(mode_details[mode].get("first_path_divergence_turns"), Counter):
            mode_details[mode]["first_path_divergence_turns"] = dict(
                sorted(mode_details[mode]["first_path_divergence_turns"].items())
            )
    return {
        "base_session_count": len(sample_ids),
        "canonical_mode": canonical,
        "all_modes_success_count": all_success,
        "all_modes_success_rate": round(all_success / len(sample_ids), 6) if sample_ids else None,
        "by_mode": mode_details,
    }


def run_language_stress(
    *,
    private_sessions_path: str | Path,
    response_bank_path: str | Path,
    catalog_path: str | Path,
    templates_path: str | Path,
    modes_path: str | Path,
    modes: Iterable[str] = DEFAULT_MODES,
    split: str = "development",
    fold: int | None = None,
    limit: int | None = None,
    sample_id: str | None = None,
    scenario: str | None = None,
    surface_seed: str = "language-stress-v1",
    max_turns: int = MAX_TURNS,
    top_k: int = TOP_K,
    config_path: str | Path | None = None,
    agent_factory: Callable[[], Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    private_rows = _load_jsonl(private_sessions_path)
    response_rows = _load_jsonl(response_bank_path)
    private_by_id = {str(row.get("sample_id")): row for row in private_rows}
    response_by_id = {str(row.get("sample_id")): row for row in response_rows}
    if len(private_by_id) != len(private_rows) or len(response_by_id) != len(response_rows):
        raise ValueError("session IDs must be unique in both V2 inputs")
    if set(private_by_id) != set(response_by_id):
        raise ValueError("private_sessions and response_bank sample IDs do not match")

    mode_names = list(dict.fromkeys(modes))
    if not mode_names:
        raise ValueError("at least one language mode is required")
    available_modes = _load_modes(modes_path)
    unknown_modes = set(mode_names) - set(available_modes)
    if unknown_modes:
        raise ValueError("unknown language modes: %s" % sorted(unknown_modes))
    if fold is not None and fold < 0:
        raise ValueError("fold must be non-negative when supplied")
    if max_turns < 1 or max_turns > MAX_TURNS:
        raise ValueError("max_turns must be in [1, %d]" % MAX_TURNS)
    if top_k < 1:
        raise ValueError("top_k must be positive")
    renderer = SurfaceRenderer(templates_path, modes_path)

    selected: list[dict[str, Any]] = []
    for row in private_rows:
        if split != "all" and str(row.get("split") or "") != split:
            continue
        if fold is not None and int(row.get("fold", -1)) != fold:
            continue
        if sample_id and str(row.get("sample_id")) != sample_id:
            continue
        if scenario and str(row.get("scenario_type")) != scenario:
            continue
        selected.append(row)
    selected.sort(key=lambda row: str(row.get("sample_id")))
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive when supplied")
        selected = selected[:limit]
    if not selected:
        raise ValueError("no V2 sessions match the requested filters")

    if agent_factory is None:
        agent_factory = lambda: Agent(catalog_path, config_path=config_path)
    agent = agent_factory()
    catalog_ids = set(getattr(getattr(agent, "catalog", None), "products", {}) or {})
    if not catalog_ids:
        with Path(catalog_path).open(encoding="utf-8") as handle:
            catalog_ids = {
                str(json.loads(line).get("parent_asin") or "")
                for line in handle
                if line.strip()
            }

    traces: list[dict[str, Any]] = []
    traces_by_sample: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    template_usage: Counter[tuple[str, str, str]] = Counter()
    diagnostics: Counter[str] = Counter()
    for sample in selected:
        sid = str(sample["sample_id"])
        session = response_by_id[sid]
        private_card = sample.get("intent_card")
        private_behavior = sample.get("behavior")
        if session.get("ground_truth") != sample.get("ground_truth"):
            raise ValueError("target mismatch for sample %s" % sid)
        if session.get("intent_card") != private_card or session.get("behavior") != private_behavior:
            raise ValueError("hidden-field mismatch for sample %s" % sid)
        for mode_name in mode_names:
            trace = run_trajectory(
                sample,
                session,
                renderer,
                agent,
                catalog_ids,
                mode_name=mode_name,
                surface_seed=surface_seed,
                max_turns=max_turns,
                top_k=top_k,
            )
            traces.append(trace)
            traces_by_sample[sid][mode_name] = trace
            for turn in trace["turns"]:
                template_usage[(mode_name, str(turn["speech_act"]), str(turn["template_id"]))] += 1
                diagnostics.update(str(item) for item in turn.get("diagnostics") or [])
                diagnostics.update(
                    str(item) for item in turn.get("next_event_diagnostics") or []
                )
                speech_act = str(turn.get("speech_act") or "")
                if speech_act == "missing_attribute_fallback":
                    diagnostics["fallback_count"] += 1
                elif speech_act == "no_preference":
                    diagnostics["no_preference_count"] += 1
                elif speech_act == "boundary_no_preference":
                    diagnostics["boundary_count"] += 1
                elif speech_act == "intent_override":
                    diagnostics["override_count"] += 1

    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_scenario: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        by_mode[str(trace["mode"])].append(trace)
        by_scenario[(str(trace["mode"]), str(trace["scenario_type"]))].append(trace)
    overall = _group_metrics(traces, max_turns=max_turns)
    overall.update({
        "base_session_count": len(selected),
        "trajectory_count": len(traces),
        "mode_count": len(mode_names),
        "aggregation": "equal-weight paired mode trajectories",
    })
    summary = {
        "schema_version": 1,
        "driver": {
            "name": "v2_language_stress_driver",
            "version": DRIVER_VERSION,
            "surface_seed": surface_seed,
            "max_turns": max_turns,
            "top_k": top_k,
            "modes": mode_names,
            "agent_hidden_fields": False,
        },
        "inputs": {
            "private_sessions": {
                "path": str(Path(private_sessions_path)),
                "sha256": _sha256_file(private_sessions_path),
            },
            "response_bank": {
                "path": str(Path(response_bank_path)),
                "sha256": _sha256_file(response_bank_path),
            },
            "templates": {
                "path": str(Path(templates_path)),
                "sha256": _sha256_file(templates_path),
            },
            "modes": {
                "path": str(Path(modes_path)),
                "sha256": _sha256_file(modes_path),
            },
            "config": (
                {
                    "path": str(Path(config_path)),
                    "sha256": _sha256_file(config_path),
                }
                if config_path is not None
                else None
            ),
        },
        "selection": {
            "split": split,
            "fold": fold,
            "scenario": scenario,
            "sample_id": sample_id,
            "base_session_count": len(selected),
            "trajectory_count": len(traces),
        },
        "overall": overall,
        "by_mode": {
            mode: _group_metrics(rows, max_turns=max_turns)
            for mode, rows in sorted(by_mode.items())
        },
        "by_mode_and_scenario": {
            "%s:%s" % key: _group_metrics(rows, max_turns=max_turns)
            for key, rows in sorted(by_scenario.items())
        },
        "paired_robustness": _paired_summary(
            traces_by_sample, mode_names, max_turns=max_turns
        ),
        "reported_token_usage": _usage_summary(traces),
        "latency_ms": _latency_summary(traces),
        "template_usage": {
            "%s:%s:%s" % key: count
            for key, count in sorted(template_usage.items())
        },
        "diagnostics": dict(sorted(diagnostics.items())),
        "notes": [
            "Metrics are aggregated by base semantic session; language trajectories are paired variants.",
            "The driver scores ask_attribute structurally, matching the official protocol; natural-language question mismatch is reported only indirectly.",
            "Synthetic V2 hidden fields are not organizer ground truth or an official private score.",
        ],
    }
    return summary, traces
