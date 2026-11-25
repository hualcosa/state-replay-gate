"""Scenario loader and the snapshot invariant the gate relies on (P8 stable)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from srg.contracts import Scenario, WorldState
from srg.state import apply_patch


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def p8_eligible(ticket_id: str, state: WorldState) -> bool:
    """Policy P8: the ticket was opened within ``refund_window_days`` of ``delivered_at``.

    False when the order is undelivered or the ticket is not in ``state``.
    """
    ticket = next((t for t in state.tickets if t.ticket_id == ticket_id), None)
    if ticket is None or state.order.delivered_at is None:
        return False
    elapsed = _ts(ticket.opened_at) - _ts(state.order.delivered_at)
    return elapsed.days <= state.policy.refund_window_days


def check_p8_stable(scenario: Scenario, s1: WorldState) -> None:
    """Raise ValueError if P8 eligibility differs between S0 and S1 (review round 3, finding 2).

    The nine gate rules deliberately have no P8 rule: the refund window is assumed not to change
    between snapshots. A scenario whose mutation flips P8 would let D pay a refund the policy
    denies, so it is rejected instead of silently relying on that assumption.
    """
    before = p8_eligible(scenario.ticket_id, scenario.t0.state)
    after = p8_eligible(scenario.ticket_id, s1)
    if before != after:
        raise ValueError(
            f"scenario {scenario.id}: the mutation changes P8 eligibility of ticket "
            f"{scenario.ticket_id} (refund window: {'eligible' if before else 'ineligible'} at "
            f"T0, {'eligible' if after else 'ineligible'} at T1); the gate has no P8 rule, so "
            "scenarios must keep P8 unchanged between snapshots"
        )


def load_scenario(path: Path) -> Scenario:
    """Load and validate one scenario file. Raises ValueError naming the file."""
    path = Path(path)
    try:
        scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid scenario: {exc}") from exc
    try:
        s1 = apply_patch(scenario.t0.state, scenario.mutation.ops)
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"{path}: mutation does not apply to the T0 state: {exc}") from exc
    try:
        check_p8_stable(scenario, s1)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return scenario


def load_scenarios(directory: Path) -> list[Scenario]:
    """All ``*.json`` in ``directory`` (not recursive), sorted by id; raises on bad/duplicate."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"scenario directory not found: {directory}")
    seen: dict[str, Path] = {}
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.json")):
        scenario = load_scenario(path)
        if scenario.id in seen:
            raise ValueError(
                f"{path}: duplicate scenario id {scenario.id} (also in {seen[scenario.id]})"
            )
        seen[scenario.id] = path
        scenarios.append(scenario)
    return sorted(scenarios, key=lambda s: s.id)
