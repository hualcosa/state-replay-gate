"""Review round 2, finding 1: a refund for the wrong amount is not a correct refund.

Reproduction: S09/C's recorded tool response is changed from 5800 to 1 in memory and sent
through the real parser and runner (replay only; a recording miss raises, no network).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from srg.arms import nonconforming_arms, run_scenario, run_scenarios, verify_artifact
from srg.contracts import RunArtifact
from srg.render import NONCONFORMING, make_console, render_run, render_verify
from srg.scenarios import load_scenarios


def _agent(repo_root: Path, *, target: str = "S09/C", amount: int = 1):
    """A replay RefundAgent whose response for ``target`` pays ``amount`` minor units."""
    from srg.agent import RefundAgent

    agent = RefundAgent.from_env(mode="replay", recordings_dir=repo_root / "recordings")
    real_chat = agent.client.chat

    def chat(request, *, sample=0, label):
        response, ref = real_chat(request, sample=sample, label=label)
        if label == target:
            response = copy.deepcopy(response)
            fn = response["choices"][0]["message"]["tool_calls"][0]["function"]
            args = json.loads(fn["arguments"])
            assert args["amount_minor"] == 5800
            args["amount_minor"] = amount
            fn["arguments"] = json.dumps(args)
        return response, ref

    agent.client.chat = chat  # type: ignore[method-assign]
    return agent


@pytest.fixture(scope="module")
def scenarios(repo_root: Path):
    return load_scenarios(repo_root / "scenarios")


@pytest.fixture(scope="module")
def s09(scenarios):
    (s,) = [s for s in scenarios if s.id == "S09"]
    assert s.ticket.requested_minor == 5800
    return s


def test_s09_c_paying_one_cent_is_blocked_in_d_and_flagged_in_c(repo_root, s09):
    run = run_scenario(s09, _agent(repo_root))
    c, d = run.arms["C"], run.arms["D"]
    assert (c.decision.amount_minor, c.decision.currency) == (1, "USD")
    assert d.decision == c.decision
    assert (d.gate.verdict, d.gate.failed_rules) == ("block", ["amount_matches_request"])
    rule = next(r for r in d.gate.results if r.rule_id == "amount_matches_request")
    assert rule.observed == {
        "requested_minor": 5800,
        "requested_currency": "USD",
        "action_amount_minor": 1,
        "action_currency": "USD",
        "ticket_found": True,
    }
    assert rule.message == f"refund 0.01 USD ≠ requested 58.00 USD ({s09.ticket.ticket_id})"
    assert not d.execution.executed and d.outcome.disposition == "blocked"
    # §5.6 classification is unchanged (C paid and the scenario should pay); the amount is
    # reported separately.
    assert c.execution.executed and c.outcome.classification == "correct_payment"
    assert nonconforming_arms(run) == ["C"]


def test_verify_and_cli_report_the_nonconforming_payment(repo_root, scenarios, capsys):
    agent = _agent(repo_root)
    art = run_scenarios(scenarios, agent, model=agent.model)
    problems, regressions, notes = verify_artifact(art)
    assert problems == []  # a model decision is not a contract violation
    assert [r[:4] for r in regressions] == ["S09:"]  # C's amount differs from T0's
    ticket = next(s.ticket for s in scenarios if s.id == "S09").ticket_id
    # Review round 3, finding 6: the operator sees currency, not raw minor units.
    assert ticket == "TCK-90109"
    assert notes == ["S09/C: paid $0.01, ticket TCK-90109 requested $58.00 (nonconforming amount)"]
    render_run(make_console(), art, out="runs/x.json")
    out = capsys.readouterr().out
    assert NONCONFORMING in out
    assert "1 paid refund(s) differ" in out
    render_verify(make_console(), "x.json", problems, regressions, notes)
    out = capsys.readouterr().out
    assert "note: S09/C: paid $0.01, ticket TCK-90109 requested $58.00" in out
    assert "verify ok" in out and "paid 1 USD" not in out


def test_real_run_has_no_nonconforming_payment(repo_root, scenarios, capsys):
    from srg.agent import RefundAgent

    agent = RefundAgent.from_env(mode="replay", recordings_dir=repo_root / "recordings")
    art = run_scenarios(scenarios, agent, model=agent.model)
    assert verify_artifact(art)[2] == []
    render_run(make_console(), art)
    assert NONCONFORMING not in capsys.readouterr().out


def test_verify_fails_a_d_that_allowed_a_nonconforming_amount(repo_root, scenarios):
    from srg.agent import RefundAgent

    agent = RefundAgent.from_env(mode="replay", recordings_dir=repo_root / "recordings")
    tampered: RunArtifact = run_scenarios(scenarios, agent, model=agent.model)
    s09 = next(r for r in tampered.scenarios if r.scenario.id == "S09")
    for arm_id in ("C", "D"):
        arm = s09.arms[arm_id]
        arm.decision.amount_minor = 1
        arm.execution.refund.amount_minor = 1
        arm.execution.total_refunded_minor = 1
    assert s09.arms["D"].gate.verdict == "allow"  # the stored verdict still says allow
    problems, _, notes = verify_artifact(tampered)
    assert "S09/D: gate verdict does not recompute from t1_state" in problems
    assert {n[:6] for n in notes} == {"S09/C:", "S09/D:"}


def test_format_money_shows_currency_not_minor_units():
    from srg.arms import format_money

    assert format_money(1, "USD") == "$0.01"
    assert format_money(5800, "USD") == "$58.00"
    assert format_money(123456, "EUR") == "€1,234.56"
    assert format_money(5800, "CHF") == "58.00 CHF"
