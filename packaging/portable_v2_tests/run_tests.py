"""Run the two portable V2 evaluations against a teammate project."""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import sys
import types
from pathlib import Path


BUNDLE_ROOT = Path(__file__).resolve().parent
ENGINE_ROOT = BUNDLE_ROOT / "engine"


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError("project root does not exist: %s" % path)
    return path


def _resolve_config(project_root: Path, explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        path = Path(explicit).expanduser()
        candidates.extend([path, project_root / path] if not path.is_absolute() else [path])
    candidates.extend([
        project_root / "configs" / "final.json",
        project_root / "configs" / "baseline.json",
        BUNDLE_ROOT / "configs" / "portable_agent.json",
    ])
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    raise FileNotFoundError("no usable Agent config was found")


def _compatible_agent(original_agent: type) -> type:
    """Adapt the usual Agent constructor to the runner's optional config argument."""

    signature = inspect.signature(original_agent)
    parameters = signature.parameters
    accepts_kwargs = any(
        item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
    )
    accepts_varargs = any(
        item.kind == inspect.Parameter.VAR_POSITIONAL for item in parameters.values()
    )

    class CompatibleAgent:
        def __init__(self, catalog_path: str | Path, config_path: str | Path | None = None) -> None:
            args: list[object] = []
            kwargs: dict[str, object] = {}
            catalog_parameter = parameters.get("catalog_path")
            if catalog_parameter is not None:
                if catalog_parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                    kwargs["catalog_path"] = catalog_path
                else:
                    args.append(catalog_path)
            elif accepts_varargs:
                args.append(catalog_path)

            if config_path is not None:
                os.environ["SHOPPING_COPILOT_CONFIG"] = str(config_path)
                if "config_path" in parameters or accepts_kwargs:
                    kwargs["config_path"] = config_path
            self._impl = original_agent(*args, **kwargs)

        def reset(self, session_id: str, user_profile: dict) -> None:
            self._impl.reset(session_id, user_profile)

        def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
            return self._impl.respond(session_id, user_message, turn, top_k)

    CompatibleAgent.__name__ = "Agent"
    CompatibleAgent.__qualname__ = "Agent"
    return CompatibleAgent


def _install_agent_adapter(project_root: Path, module_name: str) -> str:
    sys.path.insert(0, str(project_root))
    target_module = importlib.import_module(module_name)
    original_agent = getattr(target_module, "Agent", None)
    if not inspect.isclass(original_agent):
        raise TypeError("%s.Agent must be a class" % module_name)

    adapter = types.ModuleType("starter.agent")
    adapter.Agent = _compatible_agent(original_agent)
    starter_package = sys.modules.get("starter")
    if starter_package is None:
        starter_package = types.ModuleType("starter")
        starter_package.__path__ = [str(project_root / "starter")]
        sys.modules["starter"] = starter_package
    setattr(starter_package, "agent", adapter)
    sys.modules["starter.agent"] = adapter
    return "%s.Agent" % module_name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run portable V2 standard-session and wording-pressure tests."
    )
    parser.add_argument("--project-root", default=".", help="Project containing the Agent implementation.")
    parser.add_argument("--agent-module", default="starter.agent")
    parser.add_argument("--config", help="Agent config; defaults to configs/final.json.")
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Base-session limit; use 0 for the complete selected split.",
    )
    parser.add_argument("--modes", default="canonical,explicit,conversational,contextual")
    parser.add_argument("--trace-level", choices=("none", "failures", "all"), default="none")
    parser.add_argument("--output-root", help="Defaults to <project-root>/artifacts.")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be non-negative")

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    project_root = _resolve_project_path(args.project_root)
    config_path = _resolve_config(project_root, args.config)
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if args.output_root
        else project_root / "artifacts"
    )
    os.chdir(project_root)
    loaded_agent = _install_agent_adapter(project_root, args.agent_module)
    os.environ["PORTABLE_TARGET_PROJECT_ROOT"] = str(project_root)
    sys.path.insert(0, str(ENGINE_ROOT))

    from portable_v2_engine.runner import main as runner_main

    runner_args = [
        "portable_v2_runner",
        "--testkit", str(BUNDLE_ROOT),
        "--config", str(config_path),
        "--output-root", str(output_root),
        "--split", args.split,
        "--modes", args.modes,
        "--trace-level", args.trace_level,
    ]
    if args.limit:
        runner_args.extend(["--limit", str(args.limit)])
    if args.run_id:
        runner_args.extend(["--run-id", args.run_id])
    print("Agent: %s" % loaded_agent)
    print("Project: %s" % project_root)
    print("Config: %s" % config_path)
    previous_argv = sys.argv
    try:
        sys.argv = runner_args
        return runner_main()
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    raise SystemExit(main())
