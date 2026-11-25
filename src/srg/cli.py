"""``srg`` command line.

Commands: ``scenarios``, ``run``, ``story ID``, ``validate FILE``,
``verify FILE [--recordings DIR]``.
Exit codes: 0 ok · 1 verify failed or other error · 2 recording miss · 3 contract validation.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from srg.canonical import pretty_json
from srg.contracts import DecisionProvider, RecordingMissError, RunArtifact, Scenario
from srg.scenarios import load_scenarios

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_RECORDING_MISS = 2
EXIT_VALIDATION = 3
DEFAULT_MODEL = "claude-sonnet-5-5"


class CliError(Exception):
    """A user-facing error that maps to exit code 1."""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="srg", description="State Replay Gate: snapshot evals vs. action-time state checks."
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def scenarios_dir(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--scenarios-dir",
            default="scenarios",
            help="scenario JSON directory (default: %(default)s)",
        )

    sp = sub.add_parser("scenarios", help="list scenarios")
    scenarios_dir(sp)

    sp = sub.add_parser("run", help="run every scenario through the four arms")
    scenarios_dir(sp)
    sp.add_argument("--scenario", action="append", default=[], metavar="ID", help="repeatable")
    sp.add_argument("--mode", choices=["replay", "record", "live"], default=None)
    sp.add_argument("--model", default=None)
    sp.add_argument(
        "--out", default="runs/latest.json", help="artifact path (default: %(default)s)"
    )
    sp.add_argument("--with-timings", action="store_true", help="measure gate latency")
    sp.add_argument("--json", action="store_true", help="print the summary as JSON, no tables")
    sp.add_argument("--recordings-dir", default="recordings")
    sp.add_argument("--provider", choices=["agent", "scripted"], default="agent")

    sp = sub.add_parser("story", help="show one scenario's story, mutation, diff and expectation")
    scenarios_dir(sp)
    sp.add_argument("id")

    sp = sub.add_parser("validate", help="validate a run artifact against the contracts")
    sp.add_argument("file")

    sp = sub.add_parser(
        "verify",
        help=(
            "check a run artifact's internal consistency (C9); with --recordings, also check "
            "every LLM call against the recordings"
        ),
        description=(
            "Recompute everything derivable from the artifact (S1, diffs, executions, gate "
            "verdicts, outcomes, summary, run id) and compare. On its own this is an internal "
            "consistency check of the artifact, not proof of recording provenance. With "
            "--recordings DIR it also rebuilds each request, checks its hash, replays that "
            "recording offline and checks the decision equals the artifact's."
        ),
    )
    sp.add_argument("file")
    sp.add_argument(
        "--recordings",
        metavar="DIR",
        default=None,
        help="also check every LLM call against the recordings in DIR (offline replay)",
    )
    return p


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load(directory: str) -> list[Scenario]:
    return load_scenarios(Path(directory))


def _find(scenarios: list[Scenario], ids: list[str]) -> list[Scenario]:
    if not ids:
        return scenarios
    by_id = {s.id: s for s in scenarios}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise CliError(f"unknown scenario id(s): {', '.join(missing)}; known: {', '.join(by_id)}")
    return [s for s in scenarios if s.id in set(ids)]


def _read_artifact(file: str) -> RunArtifact:
    path = Path(file)
    if not path.is_file():
        raise CliError(f"file not found: {file}")
    # strict: JSON types must match the contract exactly ("false" is not a bool), as in the
    # exported JSON Schema the web page validates against.
    return RunArtifact.model_validate_json(path.read_bytes(), strict=True)


def _build_provider(args: argparse.Namespace, scenarios: list[Scenario]) -> DecisionProvider:
    if args.provider == "scripted":
        from srg.scripted import ScriptedProvider

        return ScriptedProvider.from_expected(scenarios)
    from srg.agent import RefundAgent  # lazy: task 2b; only needed for --provider agent

    return RefundAgent.from_env(
        mode=args.mode, recordings_dir=Path(args.recordings_dir), model=args.model
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _cmd_scenarios(args: argparse.Namespace) -> int:
    from srg.render import make_console, render_scenarios

    render_scenarios(make_console(), _load(args.scenarios_dir))
    return EXIT_OK


def _cmd_story(args: argparse.Namespace) -> int:
    from srg.render import make_console, render_story

    (scenario,) = _find(_load(args.scenarios_dir), [args.id])
    render_story(make_console(), scenario)
    return EXIT_OK


def _cmd_run(args: argparse.Namespace, provider: DecisionProvider | None) -> int:
    from srg.arms import run_scenarios

    scenarios = _find(_load(args.scenarios_dir), args.scenario)
    if not scenarios:
        raise CliError(f"no scenarios found in {args.scenarios_dir}")
    if provider is None:
        provider = _build_provider(args, scenarios)
    model = (
        getattr(provider, "model", None)
        or args.model
        or os.environ.get("SRG_MODEL")
        or DEFAULT_MODEL
    )
    client = getattr(provider, "client", None)  # RefundAgent keeps its mode on its LlmClient
    mode = (
        getattr(client, "mode", None)
        or getattr(provider, "mode", None)
        or args.mode
        or os.environ.get("SRG_LLM_MODE")
    )
    artifact = run_scenarios(
        scenarios,
        provider,
        llm_mode=mode or "replay",
        model=str(model),
        timed=args.with_timings,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(pretty_json(artifact), encoding="utf-8")
    if args.json:
        sys.stdout.write(pretty_json(artifact.summary))
    else:
        from srg.render import make_console, render_run

        render_run(make_console(), artifact, out=str(out))
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    from srg.render import make_console

    artifact = _read_artifact(args.file)
    make_console().print(
        f"[bold green]✓ valid run artifact[/] {args.file} "
        f"({len(artifact.scenarios)} scenarios, schema {artifact.schema_version})"
    )
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    from srg.arms import verify_artifact
    from srg.render import make_console, render_verify

    artifact = _read_artifact(args.file)
    problems, regressions, nonconforming = verify_artifact(artifact)
    if args.recordings is not None:
        from srg.provenance import verify_recordings

        problems += verify_recordings(artifact, Path(args.recordings))
    render_verify(
        make_console(),
        args.file,
        problems,
        regressions,
        nonconforming,
        recordings=args.recordings,
    )
    return EXIT_ERROR if problems else EXIT_OK


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _err(message: str) -> None:
    print(f"srg: {message}", file=sys.stderr)


def _validation_cause(exc: BaseException) -> ValidationError | None:
    while exc is not None:
        if isinstance(exc, ValidationError):
            return exc
        exc = exc.__cause__  # type: ignore[assignment]
    return None


def main(argv: list[str] | None = None, *, provider: Any = None) -> int:
    """Run the CLI. ``provider`` overrides ``--provider`` (tests inject a DecisionProvider)."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "scenarios":
            return _cmd_scenarios(args)
        if args.command == "story":
            return _cmd_story(args)
        if args.command == "run":
            return _cmd_run(args, provider)
        if args.command == "validate":
            return _cmd_validate(args)
        if args.command == "verify":
            return _cmd_verify(args)
    except RecordingMissError as exc:
        _err(
            f"recording miss: label={exc.label} request_hash={exc.request_hash} "
            f"sample={exc.sample}. Replay never calls the network; record first with --mode record."
        )
        return EXIT_RECORDING_MISS
    except Exception as exc:  # map every failure to a documented exit code
        validation = _validation_cause(exc)
        if validation is not None:
            prefix = f"{exc}" if validation is not exc else ""
            _err(f"contract validation error: {prefix or validation}")
            return EXIT_VALIDATION
        _err(f"{type(exc).__name__}: {exc}" if not isinstance(exc, CliError) else str(exc))
        return EXIT_ERROR
    raise AssertionError(f"unhandled command {args.command}")  # pragma: no cover
