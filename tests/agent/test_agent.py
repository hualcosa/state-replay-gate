from __future__ import annotations

import json
import re

import pytest
from agent_helpers import KEY, NO_NETWORK, FakeProxy, make_request, refund_args, tool_response

from srg import agent as agent_mod
from srg.agent import AgentOutputError, RefundAgent
from srg.contracts import DecisionRequest, RecordingMissError
from srg.llm import LlmClient


def make_agent(tmp_path, mode="record", proxy=None, model="claude-sonnet-5-5"):
    c = LlmClient(
        mode=mode,
        recordings_dir=tmp_path,
        base_url="http://proxy.test",
        api_key=KEY,
        transport=proxy.transport if proxy else NO_NETWORK,
    )
    return RefundAgent(client=c, model=model)


def test_build_request_shape(tmp_path, f01):
    body = make_agent(tmp_path).build_request(make_request(f01))
    assert set(body) == {"model", "messages", "tools", "tool_choice", "max_tokens"}
    assert body["tool_choice"] == "required" and body["max_tokens"] == 1024
    assert "temperature" not in body
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    names = [t["function"]["name"] for t in body["tools"]]
    assert names == ["issue_refund", "deny_refund", "escalate_to_human"]
    for t in body["tools"]:
        p = t["function"]["parameters"]
        assert p["additionalProperties"] is False and p["required"] == list(p["properties"])
    refund = body["tools"][0]["function"]["parameters"]["properties"]
    assert refund["amount_minor"]["type"] == "integer" and refund["amount_minor"]["minimum"] == 1
    assert refund["currency"]["pattern"] == "^[A-Z]{3}$"


def test_build_request_deterministic_and_label_sample_independent(tmp_path, f01):
    a = make_agent(tmp_path)
    r0 = make_request(f01, label="F01/t0", sample=0)
    r1 = make_request(f01, label="F01/C", sample=1)
    assert a.build_request(r0) == a.build_request(r0) == a.build_request(r1)
    changed = f01.t0.state.model_copy(deep=True)
    changed.customer.fraud_flag = True
    other = r0.model_copy(update={"state": changed})
    assert a.build_request(other) != a.build_request(r0)


def test_memory_note_request(tmp_path, f01):
    req = make_request(f01)
    note_req = DecisionRequest(
        label="F01/B",
        ticket=req.ticket,
        context_kind="memory_note",
        state=None,
        memory_note="Approved: return in transit.",
    )
    text = make_agent(tmp_path).build_request(note_req)["messages"][1]["content"]
    assert "not available" in text and "Approved: return in transit." in text
    assert "customer_id" not in text


def test_prompt_has_no_story_or_demo_vocabulary(tmp_path, f01):
    body = make_agent(tmp_path).build_request(make_request(f01, label="F01/t0"))
    blob = "\n".join(m["content"] for m in body["messages"]) + json.dumps(body["tools"])
    for leak in (
        f01.story.setup,
        f01.story.stakes,
        f01.story.twist,
        f01.mutation.narrative,
        f01.expected.why,
        "F01/t0",
        "chargeback_opened",
    ):
        assert leak not in blob
    assert not re.search(r"\b(gate|arm|T1|eval)\b", blob, re.IGNORECASE)


def test_policy_change_changes_hash_and_replay_misses(tmp_path, f01, monkeypatch):
    req = make_request(f01)
    ticket = req.ticket
    proxy = FakeProxy([tool_response("issue_refund", refund_args(ticket))])
    recorder = make_agent(tmp_path, "record", proxy)
    first = recorder.decide(req)
    replayer = make_agent(tmp_path, "replay")
    assert replayer.decide(req).decision == first.decision
    monkeypatch.setattr(agent_mod, "POLICY_V1", agent_mod.POLICY_V1 + ".")
    h1 = replayer.build_request(req)
    assert h1["messages"][0]["content"].endswith(".")
    with pytest.raises(RecordingMissError):
        replayer.decide(req)


@pytest.fixture
def parse_ctx(tmp_path, f01):
    req = make_request(f01)

    def run(response):
        proxy = FakeProxy([response])
        return make_agent(tmp_path, "live", proxy).decide(req)

    return req, run


def test_parse_valid_refund(parse_ctx):
    req, run = parse_ctx
    result = run(tool_response("issue_refund", refund_args(req.ticket)))
    d = result.decision
    assert (d.action, d.amount_minor, d.currency) == ("issue_refund", 25000, "USD")
    assert result.llm.prompt_tokens == 100 and result.llm.completion_tokens == 20
    assert result.llm.sample == 0 and result.llm.model == "claude-sonnet-5-5"


@pytest.mark.parametrize("name", ["deny_refund", "escalate_to_human"])
def test_parse_non_refund_has_null_amount(parse_ctx, name):
    req, run = parse_ctx
    args = refund_args(req.ticket)
    del args["amount_minor"], args["currency"]
    d = run(tool_response(name, args)).decision
    assert d.action == name and d.amount_minor is None and d.currency is None


def test_parse_truncates_prose(parse_ctx):
    req, run = parse_ctx
    args = refund_args(req.ticket, rationale="r" * 900, memory_note="m" * 900)
    d = run(tool_response("issue_refund", args)).decision
    assert len(d.rationale) == 600 and len(d.memory_note) == 400


def _two_calls(req):
    r = tool_response("issue_refund", refund_args(req.ticket))
    r["choices"][0]["message"]["tool_calls"] *= 2
    return r


def _zero_calls(req):
    r = tool_response("issue_refund", refund_args(req.ticket))
    r["choices"][0]["message"]["tool_calls"] = []
    r["choices"][0]["message"]["content"] = "I will refund."
    return r


def _bad_json(req):
    r = tool_response("issue_refund", refund_args(req.ticket))
    r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
    return r


BAD = {
    "zero": _zero_calls,
    "two": _two_calls,
    "unknown_tool": lambda r: tool_response("wire_money", refund_args(r.ticket)),
    "invalid_json": _bad_json,
    "amount_str": lambda r: tool_response(
        "issue_refund", refund_args(r.ticket, amount_minor="25000")
    ),
    "amount_float": lambda r: tool_response(
        "issue_refund", refund_args(r.ticket, amount_minor=250.0)
    ),
    "amount_bool": lambda r: tool_response(
        "issue_refund", refund_args(r.ticket, amount_minor=True)
    ),
    "amount_zero": lambda r: tool_response("issue_refund", refund_args(r.ticket, amount_minor=0)),
    "bad_currency": lambda r: tool_response(
        "issue_refund", refund_args(r.ticket, currency="usd")
    ),
    "wrong_ticket": lambda r: tool_response(
        "issue_refund", refund_args(r.ticket, ticket_id="TCK-OTHER")
    ),
    "wrong_order": lambda r: tool_response(
        "issue_refund", refund_args(r.ticket, order_id="ORD-OTHER")
    ),
    "missing_arg": lambda r: tool_response(
        "issue_refund", {k: v for k, v in refund_args(r.ticket).items() if k != "memory_note"}
    ),
    "malformed": lambda r: {"choices": []},
    # review round 1, finding 9: additionalProperties: false holds for incoming arguments too
    "unexpected_arg": lambda r: tool_response(
        "issue_refund", {**refund_args(r.ticket), "unexpected": True}
    ),
    "refund_fields_on_deny": lambda r: tool_response("deny_refund", refund_args(r.ticket)),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_parse_rejects(parse_ctx, case):
    req, run = parse_ctx
    with pytest.raises(AgentOutputError) as err:
        run(BAD[case](req))
    msg = str(err.value)
    assert req.label in msg and "sha256:" in msg
    assert "I will refund" not in msg and "TCK-OTHER" not in msg


def test_latest_recorded_at_and_from_env(tmp_path, f01, monkeypatch):
    req = make_request(f01)
    proxy = FakeProxy([tool_response("issue_refund", refund_args(req.ticket))])
    recorder = make_agent(tmp_path, "record", proxy)
    assert recorder.latest_recorded_at() is None
    recorder.decide(req)
    stamp = recorder.latest_recorded_at()
    assert stamp and stamp.endswith("Z")
    monkeypatch.setenv("SRG_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("SRG_LLM_MODE", "replay")
    agent = RefundAgent.from_env(mode=None, recordings_dir=tmp_path)
    assert agent.model == "claude-sonnet-5-5" and agent.client.mode == "replay"
    assert agent.decide(req).decision.action == "issue_refund"
    assert agent.latest_recorded_at() == stamp
    explicit = RefundAgent.from_env(mode="live", recordings_dir=tmp_path, model="gpt-6-sol")
    assert explicit.model == "gpt-6-sol" and explicit.client.mode == "live"
    monkeypatch.setenv("SRG_LLM_MODE", "bogus")
    with pytest.raises(ValueError):
        RefundAgent.from_env(mode=None, recordings_dir=tmp_path)
