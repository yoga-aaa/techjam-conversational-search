"""Run the two V2 evaluation modes and generate a compact report bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluator.language_stress_driver import DEFAULT_MODES, run_language_stress  # noqa: E402
from evaluator.v2_session_evaluator import evaluate_v2_sessions  # noqa: E402


RUNNER_VERSION = "2.0.0"
DEFAULT_KIT_NAME = "techjam-test-v2-simplified-20260827"
REQUIRED_ASSETS = {
    "private_sessions": Path("data/synthetic/generated/v2/private_sessions.jsonl"),
    "response_bank": Path("data/synthetic/generated/v2/response_bank.jsonl"),
    "catalog": Path("data/catalog.jsonl"),
    "templates": Path("data/templates/user_utterance_templates.json"),
    "modes": Path("configs/language_stress_modes.json"),
    "manifest": Path("package_manifest.json"),
}


@dataclass(frozen=True)
class TestkitPaths:
    root: Path
    private_sessions: Path
    response_bank: Path
    catalog: Path
    templates: Path
    modes: Path
    manifest: Path


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl_count(path: str | Path) -> int:
    with Path(path).open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _safe_run_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not cleaned:
        raise ValueError("run-id must contain at least one safe character")
    return cleaned


def resolve_testkit(explicit_path: str | Path | None = None) -> TestkitPaths:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    configured = os.getenv("V2_TESTKIT_ROOT")
    if configured:
        candidates.append(Path(configured))
    candidates.append(ROOT.parent / "_testkit" / DEFAULT_KIT_NAME)
    testkit_parent = ROOT.parent / "_testkit"
    if testkit_parent.exists():
        candidates.extend(
            sorted(
                testkit_parent.glob("techjam-test-v2-*"),
                key=lambda path: path.name,
                reverse=True,
            )
        )

    checked: list[str] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if str(resolved) in checked:
            continue
        checked.append(str(resolved))
        if not resolved.is_dir():
            continue
        paths = {name: resolved / relative for name, relative in REQUIRED_ASSETS.items()}
        if all(path.is_file() for path in paths.values()):
            return TestkitPaths(root=resolved, **paths)

    raise FileNotFoundError(
        "Could not locate a complete V2 test kit. Checked: %s"
        % (", ".join(checked) or "no candidates")
    )


def validate_testkit(paths: TestkitPaths) -> dict[str, Any]:
    """Run a preflight check; this is not a third scoring mode."""
    manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
    expected_hashes = {
        name: str((manifest.get(name) or {}).get("sha256") or "").lower()
        for name in ("private_sessions", "response_bank")
    }
    assets: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_ASSETS:
        path = getattr(paths, name)
        actual_hash = _sha256_file(path)
        expected_hash = expected_hashes.get(name) or None
        valid = expected_hash is None or actual_hash == expected_hash
        assets[name] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": actual_hash,
            "expected_sha256": expected_hash,
            "hash_valid": valid,
        }
        if path.suffix == ".jsonl":
            assets[name]["row_count"] = _jsonl_count(path)
        if not valid:
            raise ValueError(
                "%s SHA-256 mismatch: expected %s, got %s"
                % (name, expected_hash, actual_hash)
            )

    private_count = int(assets["private_sessions"]["row_count"])
    response_count = int(assets["response_bank"]["row_count"])
    if private_count != response_count:
        raise ValueError(
            "private_sessions and response_bank row counts differ: %d != %d"
            % (private_count, response_count)
        )
    return {
        "status": "passed",
        "package": {
            "schema_version": manifest.get("package_schema_version"),
            "tag": manifest.get("package_tag"),
            "variant": manifest.get("package_variant"),
        },
        "assets": assets,
    }


def _git_metadata() -> dict[str, Any]:
    def command(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status = command("status", "--porcelain")
    return {
        "commit": command("rev-parse", "HEAD") or None,
        "branch": command("branch", "--show-current") or None,
        "dirty": bool(status),
        "status": status.splitlines(),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_traces(path: Path, traces: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            handle.write(json.dumps(trace, ensure_ascii=False, sort_keys=True) + "\n")


def _metric(value: object, digits: int = 6) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _status_label(status: str) -> str:
    return {
        "passed": "PASS",
        "completed": "COMPLETED",
        "failed": "FAILED",
        "skipped": "SKIPPED",
    }.get(status, status.upper())


def _markdown_table(headers: list[str], rows: list[list[object]]) -> list[str]:
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return result


def _four_metrics(metrics: dict[str, Any]) -> list[str]:
    return [
        _metric(metrics.get("hit_rate_at_10")),
        _metric(metrics.get("mrr")),
        _metric(metrics.get("mttc")),
        _metric(metrics.get("recommended_technical_score")),
    ]


def build_markdown_report(summary: dict[str, Any]) -> str:
    run = summary["run"]
    stages = summary["stages"]
    results = summary.get("results", {})
    selection_warning = (
        "> 本次已显式打开 holdout，结果不应被用于反复调参。"
        if run.get("split", "holdout") == "holdout"
        else "> 本次使用 development 数据，可用于快速烟测。"
    )
    lines = [
        "# V2 Holdout 一键测试报告",
        "",
        "> 本报告使用 catalog 派生的合成 V2 holdout；它不是主办方私有集成绩。",
        selection_warning,
        "",
        "## 执行摘要",
        "",
        f"- 运行 ID：`{run['run_id']}`",
        f"- 总体状态：**{_status_label(run['status'])}**",
        f"- 数据选择：`{run.get('split', 'holdout')}`",
        f"- 会话上限：{_metric(run.get('limit'))}",
        f"- 总耗时：{_metric(run['duration_seconds'], 3)} 秒",
        f"- 配置：`{run['config_path']}`",
        "",
        "### 阶段状态",
        "",
    ]
    stage_rows = []
    for name in ("integrity", "sessions", "language"):
        stage = stages.get(name, {})
        stage_rows.append([
            {"integrity": "运行前数据完整性检查", "sessions": "标准会话", "language": "多语言压力"}[name],
            _status_label(str(stage.get("status", "skipped"))),
            _metric(stage.get("duration_seconds"), 3),
            stage.get("error") or "",
        ])
    lines.extend(_markdown_table(["阶段", "状态", "耗时（秒）", "错误"], stage_rows))
    lines.extend([
        "",
        "## 测试说明",
        "",
        "这里只保留两种评分模式。完整性检查只是运行前置条件，不计为测试模式。",
        "",
        "1. **标准会话**：对固定 session 运行最多 10 轮，输出 Hit Rate@10、MRR、MTTC 和技术分。",
        "2. **多语言压力**：对相同 session 生成 canonical、explicit、conversational、contextual 四种配对措辞轨迹，整体及每种模式均输出相同四项指标。",
        "",
        "技术分公式：`0.50 × Hit Rate@10 + 0.30 × MRR + 0.20 × Efficiency`，其中 `Efficiency = (11 - MTTC) / 10`。",
        "",
        "## 测试结果",
        "",
    ])

    sessions = results.get("sessions")
    if sessions:
        lines.extend(["### 模式一：标准会话", ""])
        lines.extend(_markdown_table(
            ["Sessions", "Hit Rate@10", "MRR", "MTTC", "技术分"],
            [[sessions.get("sample_count"), *_four_metrics(sessions)]],
        ))
        scenario_rows = [
            [name, metrics.get("sample_count"), *_four_metrics(metrics)[:3]]
            for name, metrics in sorted((sessions.get("scenario_metrics") or {}).items())
        ]
        if scenario_rows:
            lines.extend(["", "按场景拆分：", ""])
            lines.extend(_markdown_table(
                ["Scenario", "Sessions", "Hit Rate@10", "MRR", "MTTC"],
                scenario_rows,
            ))
        lines.append("")

    language = results.get("language")
    if language:
        overall = language.get("overall") or {}
        lines.extend(["### 模式二：多语言压力", "", "整体评分：", ""])
        lines.extend(_markdown_table(
            ["Base sessions", "Trajectories", "Hit Rate@10", "MRR", "MTTC", "技术分"],
            [[
                overall.get("base_session_count"),
                overall.get("trajectory_count"),
                *_four_metrics(overall),
            ]],
        ))
        mode_rows = [
            [name, metrics.get("sample_count"), *_four_metrics(metrics)]
            for name, metrics in sorted((language.get("by_mode") or {}).items())
        ]
        if mode_rows:
            lines.extend(["", "按措辞模式拆分：", ""])
            lines.extend(_markdown_table(
                ["Mode", "Sessions", "Hit Rate@10", "MRR", "MTTC", "技术分"],
                mode_rows,
            ))
        paired = language.get("paired_robustness") or {}
        lines.extend([
            "",
            f"- 四模式全部成功率：{_metric(paired.get('all_modes_success_rate'))}",
            f"- 四模式全部成功会话：{_metric(paired.get('all_modes_success_count'))}/{_metric(paired.get('base_session_count'))}",
            "",
        ])

    findings: list[str] = []
    if sessions:
        findings.append(
            "标准会话 Hit Rate@10=%s，技术分=%s。"
            % (_metric(sessions.get("hit_rate_at_10")), _metric(sessions.get("recommended_technical_score")))
        )
    if language:
        overall = language.get("overall") or {}
        findings.append(
            "多语言压力整体 Hit Rate@10=%s，MRR=%s，MTTC=%s，技术分=%s。"
            % tuple(_four_metrics(overall))
        )
    by_mode = (language or {}).get("by_mode") or {}
    if by_mode:
        best = max(by_mode, key=lambda name: by_mode[name]["hit_rate_at_10"])
        worst = min(by_mode, key=lambda name: by_mode[name]["hit_rate_at_10"])
        gap = by_mode[best]["hit_rate_at_10"] - by_mode[worst]["hit_rate_at_10"]
        if gap == 0:
            findings.append("各措辞模式 Hit Rate@10 相同（%s）。" % _metric(by_mode[best]["hit_rate_at_10"]))
        else:
            findings.append(
                "措辞模式 Hit Rate@10 最好为 %s（%s），最低为 %s（%s），差值 %s。"
                % (best, _metric(by_mode[best]["hit_rate_at_10"]), worst, _metric(by_mode[worst]["hit_rate_at_10"]), _metric(gap))
            )
    if not findings:
        findings.append("测试阶段未产生可汇总结果，请查看阶段错误。")
    lines.extend(["## 自动结论", "", *(f"- {finding}" for finding in findings), ""])

    lines.extend([
        "## 产物",
        "",
        "- [机器可读汇总](summary.json)",
        "- [标准会话评估明细](session_evaluation.json)",
        "- [多语言压力汇总](language_stress_summary.json)",
    ])
    trace_export = (language or {}).get("trace_export") or {}
    if trace_export.get("path"):
        lines.append("- [多语言压力轨迹](language_stress_traces.jsonl)")
    lines.extend([
        "- [执行日志](run.log)",
        "",
        "## 使用限制",
        "",
        "- 多语言压力测试衡量的是多种英文措辞风格，并非跨自然语言翻译质量。",
        "- 本报告使用的是 catalog 派生的合成 holdout，不是主办方隐藏评测集。",
        "- 查看 holdout 后若据此修改系统，应重新建立新的锁定验证集。",
        "- 结果只对应报告中记录的 Git 状态、配置文件和输入哈希。",
        "",
    ])
    return "\n".join(lines)


def _compact_results(results: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if "integrity" in results:
        compact["integrity"] = results["integrity"]
    if "sessions" in results:
        report = results["sessions"]
        compact["sessions"] = {
            key: report.get(key)
            for key in (
                "sample_count",
                "hit_rate_at_10",
                "mrr",
                "mttc",
                "efficiency",
                "recommended_technical_score",
                "scenario_metrics",
                "reported_token_usage",
                "selection",
            )
        }
    if "language" in results:
        report = results["language"]
        compact["language"] = {
            key: report.get(key)
            for key in (
                "selection",
                "overall",
                "by_mode",
                "paired_robustness",
                "reported_token_usage",
                "latency_ms",
                "trace_export",
            )
        }
    return compact


class RunLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _run_stage(
    name: str,
    stages: dict[str, dict[str, Any]],
    logger: RunLogger,
    function: Callable[[], Any],
) -> Any | None:
    logger(f"START {name}")
    started = time.perf_counter()
    try:
        result = function()
    except Exception as exc:
        duration = round(time.perf_counter() - started, 3)
        error_path = logger.path.parent / f"{name}_error.txt"
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        stages[name] = {
            "status": "failed",
            "duration_seconds": duration,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": str(error_path),
        }
        logger(f"FAILED {name} after {duration:.3f}s: {type(exc).__name__}: {exc}")
        return None
    duration = round(time.perf_counter() - started, 3)
    stages[name] = {"status": "passed", "duration_seconds": duration}
    logger(f"PASS {name} in {duration:.3f}s")
    return result


def _select_traces(traces: list[dict[str, Any]], level: str) -> list[dict[str, Any]]:
    if level == "all":
        return traces
    if level == "failures":
        return [trace for trace in traces if not trace.get("hit")]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the standard-session and language-pressure V2 evaluations."
    )
    parser.add_argument("--testkit", help="Extracted V2 test-kit root.")
    parser.add_argument(
        "--config",
        default=str(ROOT / "configs" / "final.json"),
        help="Agent config used by both evaluation modes.",
    )
    parser.add_argument("--output-root", default=str(ROOT / "artifacts"))
    parser.add_argument("--run-id", help="Optional deterministic output directory name.")
    parser.add_argument("--split", choices=("development", "holdout"), default="holdout")
    parser.add_argument("--limit", type=int, help="Optional session limit for smoke tests.")
    parser.add_argument("--modes", default=",".join(DEFAULT_MODES))
    parser.add_argument(
        "--trace-level",
        choices=("failures", "all", "none"),
        default="failures",
        help="Language trajectory detail to save; metrics always use every trajectory.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    mode_names = [value.strip() for value in args.modes.split(",") if value.strip()]
    if not mode_names:
        parser.error("--modes must contain at least one mode")

    paths = resolve_testkit(args.testkit)
    config_path = Path(args.config)
    if not config_path.is_absolute() and not config_path.exists():
        config_path = ROOT / config_path
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")

    started_at = datetime.now().astimezone()
    run_id = _safe_run_id(args.run_id or started_at.strftime("v2_holdout_%Y%m%d-%H%M%S"))
    output_dir = Path(args.output_root).resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    logger = RunLogger(output_dir / "run.log")
    logger(f"V2 two-mode runner {RUNNER_VERSION}")
    logger(f"testkit={paths.root}")
    logger(f"split={args.split} limit={args.limit}")
    logger(f"trace_level={args.trace_level}")

    stages: dict[str, dict[str, Any]] = {}
    results: dict[str, Any] = {}
    run_started = time.perf_counter()

    integrity = _run_stage("integrity", stages, logger, lambda: validate_testkit(paths))
    if integrity is not None:
        results["integrity"] = integrity

    if integrity is None:
        for name in ("sessions", "language"):
            stages[name] = {
                "status": "skipped",
                "duration_seconds": 0.0,
                "error": "test-kit integrity validation failed",
            }
    else:
        sessions = _run_stage(
            "sessions",
            stages,
            logger,
            lambda: evaluate_v2_sessions(
                dataset_path=paths.private_sessions,
                catalog_path=paths.catalog,
                config_path=config_path,
                split=args.split,
                limit=args.limit,
            ),
        )
        if sessions is not None:
            _write_json(output_dir / "session_evaluation.json", sessions)
            results["sessions"] = sessions

        language_result = _run_stage(
            "language",
            stages,
            logger,
            lambda: run_language_stress(
                private_sessions_path=paths.private_sessions,
                response_bank_path=paths.response_bank,
                catalog_path=paths.catalog,
                templates_path=paths.templates,
                modes_path=paths.modes,
                modes=mode_names,
                split=args.split,
                limit=args.limit,
                max_turns=10,
                top_k=10,
                config_path=config_path,
            ),
        )
        if language_result is not None:
            language, traces = language_result
            selected_traces = _select_traces(traces, args.trace_level)
            trace_path: Path | None = None
            if args.trace_level != "none":
                trace_path = output_dir / "language_stress_traces.jsonl"
                _write_traces(trace_path, selected_traces)
            language["trace_export"] = {
                "level": args.trace_level,
                "total_trajectory_count": len(traces),
                "exported_trajectory_count": len(selected_traces),
                "path": str(trace_path) if trace_path else None,
            }
            _write_json(output_dir / "language_stress_summary.json", language)
            results["language"] = language

    finished_at = datetime.now().astimezone()
    failed = [name for name, stage in stages.items() if stage.get("status") != "passed"]
    status = "completed" if not failed else "failed"
    artifacts = {
        "report": str(output_dir / "REPORT.md"),
        "summary": str(output_dir / "summary.json"),
        "sessions": str(output_dir / "session_evaluation.json"),
        "language_summary": str(output_dir / "language_stress_summary.json"),
        "log": str(output_dir / "run.log"),
    }
    trace_export = (results.get("language") or {}).get("trace_export") or {}
    if trace_export.get("path"):
        artifacts["language_traces"] = trace_export["path"]
    summary = {
        "schema_version": 2,
        "runner": {"name": "v2_two_mode_runner", "version": RUNNER_VERSION},
        "run": {
            "run_id": run_id,
            "status": status,
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_seconds": round(time.perf_counter() - run_started, 3),
            "project_root": str(ROOT),
            "testkit_root": str(paths.root),
            "config_path": str(config_path),
            "split": args.split,
            "limit": args.limit,
            "modes": mode_names,
            "trace_level": args.trace_level,
            "python": sys.version,
            "platform": platform.platform(),
            "git": _git_metadata(),
            "failed_stages": failed,
        },
        "stages": stages,
        "results": _compact_results(results),
        "artifacts": artifacts,
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "REPORT.md").write_text(build_markdown_report(summary), encoding="utf-8")
    logger(f"FINAL status={status}")
    logger(f"report={output_dir / 'REPORT.md'}")
    print(json.dumps({
        "status": status,
        "run_id": run_id,
        "failed_stages": failed,
        "report": str(output_dir / "REPORT.md"),
    }, ensure_ascii=False, indent=2))
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
