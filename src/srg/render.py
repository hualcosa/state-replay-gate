"""Rich terminal views for the ``srg`` CLI. Display only: nothing here feeds an artifact."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from srg.arms import nonconforming_arms
from srg.contracts import ARM_IDS, ARM_SPECS, RunArtifact, Scenario
from srg.scripted import SCRIPTED_MODEL
from srg.state import apply_patch, diff_states

CLASS_STYLE = {
    "correct_payment": "green",
    "correct_stop": "green",
    "wrong_payment": "bold red",
    "false_stop": "yellow",
}
CLASS_SHORT = {
    "correct_payment": "✓ paid",
    "correct_stop": "✓ stopped",
    "wrong_payment": "✗ WRONG PAY",
    "false_stop": "✗ false stop",
}
DISPOSITION_SHORT = {
    "paid": "paid",
    "denied": "denied",
    "escalated": "escalated",
    "blocked": "blocked",
    "held_for_review": "review",
}
ARM_NAMES = {spec.id: spec.name for spec in ARM_SPECS}


def make_console(*, stderr: bool = False) -> Console:
    """A console on the current stdout/stderr (so test capture works), 120 cols when piped."""
    console = Console(stderr=stderr, highlight=False, soft_wrap=False)
    if not console.is_terminal:
        console = Console(stderr=stderr, highlight=False, soft_wrap=False, width=120)
    return console


def money(minor: int | None, currency: str = "USD") -> str:
    if minor is None:
        return "—"
    sign = "-" if minor < 0 else ""
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{sign}{symbol}{abs(minor) // 100:,}.{abs(minor) % 100:02d}"


def _short(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# srg scenarios
# ---------------------------------------------------------------------------


def render_scenarios(console: Console, scenarios: Sequence[Scenario]) -> None:
    table = Table(title="Scenarios", box=box.ROUNDED, title_style="bold")
    table.add_column("Id", style="bold cyan", no_wrap=True)
    table.add_column("Kind")
    table.add_column("Title")
    table.add_column("T1 change", style="magenta")
    table.add_column("T0 action")
    table.add_column("Gate (expected)")
    for s in scenarios:
        exp = s.expected
        t0 = exp.t0_action + (f" {money(exp.t0_amount_minor)}" if exp.t0_amount_minor else "")
        gate = (
            f"{exp.gate.verdict} {', '.join(exp.gate.failed_rules)}".strip() if exp.gate else "—"
        )
        title = ("★ " if s.hero else "") + s.title
        table.add_row(s.id, s.kind, title, s.mutation.label, t0, gate)
    console.print(table)


# ---------------------------------------------------------------------------
# srg story ID
# ---------------------------------------------------------------------------


def render_story(console: Console, s: Scenario) -> None:
    t = s.ticket
    head = Text.assemble(
        (f"{s.id} ", "bold cyan"), (s.title, "bold"), (f"   [{s.kind}]", "dim")
    )
    story = Table.grid(padding=(0, 2))
    story.add_column(style="bold")
    story.add_column()
    story.add_row("Setup", s.story.setup)
    story.add_row("Twist", s.story.twist)
    story.add_row("Stakes", s.story.stakes)
    story.add_row(
        "Ticket",
        f"{t.ticket_id} · {t.reason_code} · {money(t.requested_minor, t.currency)} "
        f"via {t.channel} — “{t.message}”",
    )
    console.print(Panel(story, title=head, title_align="left", box=box.ROUNDED))

    m = s.mutation
    console.print(
        Panel(
            Text.assemble(
                (m.label, "bold magenta"),
                (f"  by {m.actor} at {m.at}\n", "dim"),
                m.narrative,
            ),
            title="T1 mutation",
            title_align="left",
            box=box.ROUNDED,
        )
    )

    s1 = apply_patch(s.t0.state, m.ops)
    diff = Table(title="State diff (T0 → T1)", box=box.SIMPLE_HEAD)
    diff.add_column("Path", style="cyan")
    diff.add_column("Before", style="red")
    diff.add_column("After", style="green")
    changes = diff_states(s.t0.state, s1)
    for ch in changes:
        diff.add_row(ch.path, _short(ch.before, 90), _short(ch.after, 90))
    if not changes:
        diff.add_row("(no change)", "", "")
    console.print(diff)

    exp = s.expected
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column()
    grid.add_row("T0 action", f"{exp.t0_action} {money(exp.t0_amount_minor)}".rstrip(" —"))
    grid.add_row("Pay at T1?", "yes" if exp.t1_should_pay else "no")
    if exp.harm_if_paid.type != "none":
        grid.add_row(
            "Harm if paid",
            f"{exp.harm_if_paid.type} {money(exp.harm_if_paid.amount_minor)} — "
            f"{exp.harm_if_paid.explanation}",
        )
    grid.add_row(
        "Gate",
        f"{exp.gate.verdict} {', '.join(exp.gate.failed_rules)}".strip() if exp.gate else "—",
    )
    grid.add_row("Arm C / D", f"{exp.arm_c} / {exp.arm_d}")
    grid.add_row("Why", exp.why)
    console.print(Panel(grid, title="Expected", title_align="left", box=box.ROUNDED))


# ---------------------------------------------------------------------------
# srg run
# ---------------------------------------------------------------------------


NONCONFORMING = "≠ nonconforming amount"


def _arm_cell(run, arm_id: str, nonconforming: Sequence[str] = ()) -> Text:
    arm = run.arms[arm_id]
    cls = arm.outcome.classification
    text = Text(CLASS_SHORT[cls], style=CLASS_STYLE[cls])
    detail = DISPOSITION_SHORT[arm.outcome.disposition]
    if arm.gate is not None and arm.gate.failed_rules:
        detail += ": " + ", ".join(arm.gate.failed_rules)
    text.append(f"\n{detail}", style="dim")
    if arm.decision_changed_vs_t0:
        text.append(" ↺", style="bold yellow")
    if arm_id in nonconforming:
        text.append(f"\n{NONCONFORMING}", style="bold yellow")
    return text


def render_run(console: Console, artifact: RunArtifact, *, out: str | None = None) -> None:
    info = artifact.run
    cur = info.currency

    rows = Table(
        title="Per scenario  (↺ = decision changed vs T0)", box=box.ROUNDED, title_style="bold"
    )
    rows.add_column("Id", style="bold cyan", no_wrap=True)
    rows.add_column("T1 change", style="magenta")
    rows.add_column("T0 eval", justify="center")
    for arm_id in ARM_IDS:
        rows.add_column(f"{arm_id} · {ARM_NAMES[arm_id]}")
    flagged = 0
    for run in artifact.scenarios:
        s = run.scenario
        odd = nonconforming_arms(run)
        flagged += len(odd)
        ev = Text("pass", style="green") if run.t0.eval.passed else Text("FAIL", style="bold red")
        rows.add_row(
            ("★ " if s.hero else "") + s.id,
            s.mutation.label,
            ev,
            *(_arm_cell(run, a, odd) for a in ARM_IDS),
        )
    console.print(rows)
    if flagged:
        console.print(
            Text(
                f"{NONCONFORMING}: {flagged} paid refund(s) differ from the ticket's requested "
                "amount or currency. The pay/stop classification does not check the amount.",
                style="yellow",
            )
        )

    board = Table(title="Arm scoreboard", box=box.ROUNDED, title_style="bold")
    board.add_column("Metric", style="bold")
    for arm_id in ARM_IDS:
        board.add_column(f"{arm_id} · {ARM_NAMES[arm_id]}", justify="right")
    by = artifact.summary.by_arm

    def row(label: str, fn, style_fn=None) -> None:
        cells = []
        for arm_id in ARM_IDS:
            value = fn(by[arm_id])
            style = style_fn(by[arm_id]) if style_fn else ""
            cells.append(Text(str(value), style=style))
        board.add_row(label, *cells)

    row(
        "Wrong payments (harm)",
        lambda a: f"{a.wrong_payments}  ({money(a.wrong_payment_minor, cur)})",
        lambda a: "bold red" if a.wrong_payments else "green",
    )
    row("Correct payments", lambda a: a.correct_payments)
    row("Correct stops", lambda a: a.correct_stops)
    row("False stops", lambda a: a.false_stops, lambda a: "yellow" if a.false_stops else "")
    row("Review load", lambda a: a.review_load)
    row("Decisions changed vs T0", lambda a: a.decisions_changed_vs_t0)
    row("LLM calls at T1", lambda a: a.llm_calls_at_t1)
    row(
        "Tokens at T1 (prompt/compl.)",
        lambda a: "—"
        if a.prompt_tokens_at_t1 is None
        else f"{a.prompt_tokens_at_t1:,}/{a.completion_tokens_at_t1:,}",
    )
    scripted = artifact.run.model == SCRIPTED_MODEL
    row(
        "LLM latency p50/p95 ms",
        lambda a: "—"
        if a.llm_latency_ms_p50 is None
        else "simulated (scripted)"
        if scripted
        else f"{a.llm_latency_ms_p50}/{a.llm_latency_ms_p95}",
    )
    console.print(board)

    reg = artifact.summary.regressions
    split = Text.assemble(
        ("Regression split  ", "bold"),
        (f"{reg.scenarios_with_state_change}", "bold"),
        " scenarios changed state between T0 and T1.\n",
        ("  Live state (arm A): ", "bold"),
        (f"{reg.changed_on_live_state}", "bold cyan"),
        " of them decided differently from T0 when reading the live state.\n",
        ("  Frozen state (arm C): ", "bold"),
        (
            f"{reg.changed_on_frozen_state}",
            "bold red" if reg.changed_on_frozen_state else "bold green",
        ),
        " of them decided differently from T0 on the identical request (true regressions).",
    )
    console.print(Panel(split, box=box.ROUNDED))

    s = artifact.summary
    footer = (
        f"run {info.run_id} · mode {info.llm_mode} · model {info.model} · "
        f"T0 eval {s.t0_eval_passed}/{s.t0_eval_total} · "
        f"{len(artifact.scenarios)} synthetic scenarios"
    )
    if info.recorded_at:
        footer += f" · recorded {info.recorded_at}"
    if info.timings_included:
        footer += " · timings included"
    console.print(Text(footer, style="dim"))
    if info.model == "fixture-scripted":
        console.print(
            Text("Scripted decisions (fixture-scripted): not model outputs.", style="yellow")
        )
    if out:
        console.print(Text(f"artifact → {out}", style="dim"))


# ---------------------------------------------------------------------------
# srg verify
# ---------------------------------------------------------------------------


def render_verify(
    console: Console,
    path: str,
    problems: Sequence[str],
    regressions: Sequence[str],
    nonconforming: Sequence[str] = (),
    *,
    recordings: str | None = None,
) -> None:
    for line in regressions:
        console.print(Text(f"true regression: {line}", style="yellow"))
    for line in nonconforming:
        console.print(Text(f"note: {line}", style="yellow"))
    if problems:
        body = Group(*(Text(f"✗ {p}", style="red") for p in problems))
        console.print(
            Panel(body, title=f"verify FAILED: {len(problems)} violation(s)", border_style="red")
        )
    else:
        console.print(Text(f"✓ verify ok: {path}", style="bold green"))
        if recordings is None:
            console.print(
                Text(
                    "  internally consistent artifact; recording provenance not checked "
                    "(use --recordings DIR)",
                    style="dim",
                )
            )
        else:
            console.print(
                Text(
                    f"  internally consistent, and every LLM call matches the recordings in "
                    f"{recordings}",
                    style="green",
                )
            )
