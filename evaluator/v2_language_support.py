"""Small shared helpers required by the fixed V2 language stress test."""

from __future__ import annotations

import hashlib
import json
import re
import string
from pathlib import Path
from typing import Any


ATTRIBUTE_ORDER = (
    "category",
    "material",
    "color",
    "size",
    "style",
    "brand",
    "budget",
    "feature",
    "use_case",
    "other",
)
ATTRIBUTE_LABELS = {
    "use_case": "use case",
    "other": "other requirement",
}
TEMPLATE_GROUP_RULES = {
    "initial_buying": ({"category", "value"}, {"category", "value"}),
    "initial_browsing": ({"category"}, {"category"}),
    "initial_override": ({"category", "old_value"}, {"category", "old_value"}),
    "attribute_reply": ({"attribute_label", "values"}, {"values"}),
    "no_preference": ({"attribute_label"}, {"attribute_label"}),
    "boundary": ({"attribute_label"}, {"attribute_label"}),
    "override": ({"old_value", "new_value"}, {"new_value"}),
    "fallback": (set(), set()),
}
_SPACE_RE = re.compile(r"\s+")


def normalize_text(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def join_values(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return "%s and %s" % (values[0], values[1])
    return "%s, and %s" % (", ".join(values[:-1]), values[-1])


def attribute_label(attribute: str) -> str:
    return ATTRIBUTE_LABELS.get(attribute, attribute.replace("_", " "))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(*parts: object) -> int:
    payload = "\0".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


class TemplateCatalog:
    """Validated English template catalog used by the language renderer."""

    def __init__(self, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != 1:
            raise ValueError("template schema_version must be 1")
        if payload.get("language") != "en":
            raise ValueError("language stress templates must be English")
        groups = payload.get("groups")
        if not isinstance(groups, dict):
            raise ValueError("template catalog must contain a groups object")
        if set(groups) != set(TEMPLATE_GROUP_RULES):
            raise ValueError("template groups do not match the V2 contract")

        formatter = string.Formatter()
        seen_ids: set[str] = set()
        self.groups: dict[str, list[dict[str, Any]]] = {}
        for group_name, (allowed, required) in TEMPLATE_GROUP_RULES.items():
            entries = groups[group_name]
            if not isinstance(entries, list) or not entries:
                raise ValueError("template group %s must be a non-empty list" % group_name)
            validated: list[dict[str, Any]] = []
            seen_text: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("templates in %s must be objects" % group_name)
                template_id = entry.get("template_id")
                template = entry.get("template")
                tags = entry.get("tags", [])
                author = entry.get("author")
                if not isinstance(template_id, str) or not template_id.strip():
                    raise ValueError("every template needs a template_id")
                if template_id in seen_ids:
                    raise ValueError("duplicate template_id: %s" % template_id)
                if not isinstance(template, str) or not template.strip():
                    raise ValueError("template %s has no text" % template_id)
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    raise ValueError("template %s tags must be strings" % template_id)
                if author is not None and not isinstance(author, str):
                    raise ValueError("template %s author must be a string" % template_id)

                placeholders: set[str] = set()
                for _, field_name, _, _ in formatter.parse(template):
                    if field_name is None:
                        continue
                    if not field_name or any(char in field_name for char in ".[]"):
                        raise ValueError("only simple placeholders are supported")
                    placeholders.add(field_name)
                if placeholders - allowed:
                    raise ValueError(
                        "template %s has unsupported placeholders: %s"
                        % (template_id, sorted(placeholders - allowed))
                    )
                if required - placeholders:
                    raise ValueError(
                        "template %s is missing required placeholders: %s"
                        % (template_id, sorted(required - placeholders))
                    )
                normalized = normalize_text(template).casefold()
                if normalized in seen_text:
                    raise ValueError("duplicate template text in %s" % group_name)
                seen_text.add(normalized)
                seen_ids.add(template_id)
                validated.append({
                    "template_id": template_id,
                    "template": template,
                    "tags": list(dict.fromkeys(tags)),
                    "author": author,
                })
            self.groups[group_name] = validated

    @classmethod
    def load(cls, path: str | Path) -> "TemplateCatalog":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))
