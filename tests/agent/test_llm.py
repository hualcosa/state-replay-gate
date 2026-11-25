from __future__ import annotations

import json
import logging

import httpx
import pytest
from agent_helpers import KEY, NO_NETWORK, FakeProxy, make_request, refund_args, tool_response

from srg.agent import RefundAgent
from srg.canonical import digest
from srg.contracts import Recording, RecordingMissError
from srg.llm import (
    LlmClient,
    LlmConfigError,
    LlmTransportError,
    RecordingSequenceError,
)

REQ = {"model": "m1", "messages": [{"role": "user", "content": "hi"}], "tool_choice": "required"}
RESP = {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def client(tmp_path, mode, proxy=None, **kw):
    return LlmClient(
        mode=mode,
        recordings_dir=tmp_path,
        base_url="http://proxy.test",
        api_key=kw.pop("api_key", KEY),
        transport=proxy.transport if proxy else NO_NETWORK,
        **kw,
    )


def rec_path(tmp_path, req=REQ):
    return tmp_path / req["model"] / (digest(req).removeprefix("sha256:") + ".json")


def test_record_miss_calls_once_and_writes_valid_recording(tmp_path):
    proxy = FakeProxy([RESP])
    c = client(tmp_path, "record", proxy)
    body, ref = c.chat(REQ, sample=0, label="X/t0")
    assert body == RESP and len(proxy.requests) == 1
    assert (ref.prompt_tokens, ref.completion_tokens, ref.sample) == (7, 3, 0)
    path = rec_path(tmp_path)
    recording = Recording.model_validate_json(path.read_text())
    assert recording.request == REQ and recording.labels == ["X/t0"]
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == [path.name]  # no temp left
    assert c.latest_recorded_at == recording.samples[0].recorded_at


def test_request_goes_to_chat_completions_with_bearer(tmp_path):
    proxy = FakeProxy([RESP])
    client(tmp_path, "record", proxy).chat(REQ, label="X")
    sent = proxy.requests[0]
    assert str(sent.url) == "http://proxy.test/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(sent.content) == REQ


def test_record_repeat_makes_no_request_and_merges_labels(tmp_path):
    proxy = FakeProxy([RESP])
    c = client(tmp_path, "record", proxy)
    c.chat(REQ, label="b/x")
    body, ref = c.chat(REQ, sample=0, label="a/y")
    c.chat(REQ, sample=0, label="a/y")
    assert len(proxy.requests) == 1 and body == RESP
    recording = Recording.model_validate_json(rec_path(tmp_path).read_text())
    assert recording.labels == ["a/y", "b/x"]


def test_record_sample_len_appends_and_greater_errors(tmp_path):
    proxy = FakeProxy([RESP, {**RESP, "id": "second"}])
    c = client(tmp_path, "record", proxy)
    c.chat(REQ, sample=0, label="L")
    with pytest.raises(RecordingSequenceError):
        c.chat(REQ, sample=2, label="L")
    c.chat(REQ, sample=1, label="L2")
    recording = Recording.model_validate_json(rec_path(tmp_path).read_text())
    assert [s.sample for s in recording.samples] == [0, 1]
    assert recording.samples[1].response["id"] == "second"
    assert len(proxy.requests) == 2


def test_first_record_with_sample_gt_zero_errors(tmp_path):
    with pytest.raises(RecordingSequenceError):
        client(tmp_path, "record", FakeProxy([RESP])).chat(REQ, sample=1, label="L")


def test_replay_hit_returns_stored_body_and_latency_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    client(tmp_path, "record", FakeProxy([RESP])).chat(REQ, label="L")
    path = rec_path(tmp_path)
    data = json.loads(path.read_text())
    data["samples"][0]["latency_ms"] = 1234
    path.write_text(json.dumps(data))
    before = path.read_text()
    c = LlmClient(mode="replay", recordings_dir=tmp_path, transport=NO_NETWORK)
    body, ref = c.chat(REQ, sample=0, label="other")
    assert body == RESP and ref.latency_ms == 1234
    assert path.read_text() == before  # nothing written, label not merged
    assert c.latest_recorded_at == data["samples"][0]["recorded_at"]


def test_replay_misses_raise_and_write_nothing(tmp_path):
    c = LlmClient(mode="replay", recordings_dir=tmp_path, transport=NO_NETWORK)
    with pytest.raises(RecordingMissError):
        c.chat(REQ, label="L")
    client(tmp_path, "record", FakeProxy([RESP])).chat(REQ, label="L")
    files = sorted(tmp_path.rglob("*"))
    with pytest.raises(RecordingMissError):
        c.chat(REQ, sample=1, label="L")
    with pytest.raises(RecordingMissError):
        c.chat({**REQ, "max_tokens": 5}, label="L")
    assert sorted(tmp_path.rglob("*")) == files


def test_replay_checks_stored_request_equals_body(tmp_path):
    client(tmp_path, "record", FakeProxy([RESP])).chat(REQ, label="L")
    path = rec_path(tmp_path)
    data = json.loads(path.read_text())
    data["request"]["messages"][0]["content"] = "tampered"
    # keep the file schema-valid by rehashing the tampered request under the old name
    data["request_hash"] = digest(data["request"])
    path.write_text(json.dumps(data))
    c = LlmClient(mode="replay", recordings_dir=tmp_path, transport=NO_NETWORK)
    with pytest.raises(RecordingMissError):
        c.chat(REQ, label="L")


def test_live_calls_and_writes_nothing(tmp_path):
    proxy = FakeProxy([RESP])
    c = client(tmp_path, "live", proxy)
    body, ref = c.chat(REQ, label="L")
    assert body == RESP and len(proxy.requests) == 1 and ref.latency_ms >= 0
    assert list(tmp_path.iterdir()) == []
    assert c.latest_recorded_at is None


def test_missing_key_names_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    c = LlmClient(mode="live", recordings_dir=tmp_path, transport=NO_NETWORK)
    with pytest.raises(LlmConfigError, match="CLIPROXY_API_KEY"):
        c.chat(REQ, label="L")


def test_key_and_base_url_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPROXY_API_KEY", KEY)
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://env.test/")
    proxy = FakeProxy([RESP])
    LlmClient(mode="live", recordings_dir=tmp_path, transport=proxy.transport).chat(REQ, label="L")
    assert str(proxy.requests[0].url) == "http://env.test/v1/chat/completions"
    assert proxy.requests[0].headers["authorization"] == f"Bearer {KEY}"


def test_non_2xx_and_transport_failure(tmp_path):
    with pytest.raises(LlmTransportError, match=r"status 500.*label=L"):
        client(tmp_path, "live", FakeProxy(status=500)).chat(REQ, label="L")

    def boom(request):
        raise httpx.ConnectError("refused " + KEY)

    c = LlmClient(
        mode="live",
        recordings_dir=tmp_path,
        api_key=KEY,
        transport=httpx.MockTransport(boom),
    )
    with pytest.raises(LlmTransportError, match="label=L") as err:
        c.chat(REQ, label="L")
    assert KEY not in str(err.value)


def test_no_secrets_in_recordings_logs_or_errors(tmp_path, caplog, f01, monkeypatch):
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    caplog.set_level(logging.DEBUG)
    agent_req = make_request(f01)
    ticket = agent_req.ticket
    proxy = FakeProxy([tool_response("issue_refund", refund_args(ticket)), {"x": 1}])
    texts = []
    rec_dir = tmp_path / "rec"
    c = client(rec_dir, "record", proxy)
    agent = RefundAgent(client=c)
    agent.decide(agent_req)
    c_live = client(tmp_path / "live", "live", proxy)
    with pytest.raises(Exception) as bad:  # {"x": 1} is a malformed completion
        RefundAgent(client=c_live).decide(make_request(f01, label="F01/A"))
    texts.append(str(bad.value))
    with pytest.raises(LlmTransportError) as err:
        client(tmp_path / "e", "live", FakeProxy(status=401)).chat(REQ, label="L")
    texts.append(str(err.value))
    with pytest.raises(LlmConfigError) as cfg:
        LlmClient(mode="live", recordings_dir=tmp_path, api_key="", transport=NO_NETWORK).chat(
            REQ, label="L"
        )
    texts.append(str(cfg.value))
    for f in rec_dir.rglob("*.json"):
        texts.append(f.read_text())
    texts.append(caplog.text)
    assert (tmp_path / "live").exists() is False
    for text in texts:
        for needle in (KEY, "Authorization", "Bearer", "sk-"):
            assert needle.lower() not in text.lower()


@pytest.mark.parametrize("mode", ["replay", "record", "live"])
def test_negative_sample_is_rejected_before_indexing(tmp_path, mode):
    """Review round 1, finding 8: sample=-1 must not serve the last recorded sample."""
    proxy = FakeProxy([RESP, {**RESP, "id": "second"}, {**RESP, "id": "third"}])
    recorder = client(tmp_path, "record", proxy)
    for i in range(3):
        recorder.chat(REQ, sample=i, label="L")
    sent = len(proxy.requests)
    with pytest.raises(ValueError, match="sample must be >= 0"):
        client(tmp_path, mode, proxy).chat(REQ, sample=-1, label="L")
    assert len(proxy.requests) == sent  # nothing reached the transport


@pytest.mark.parametrize("model", ["../escape", "a/b", "..", "/abs", ""])
def test_model_id_cannot_escape_recordings_dir(tmp_path, model):
    from srg.llm import LlmClient

    client = LlmClient(mode="replay", recordings_dir=tmp_path / "recordings")
    with pytest.raises(ValueError):
        client._path(model, "sha256:" + "0" * 64)


def test_request_hash_must_be_hex(tmp_path):
    from srg.llm import LlmClient

    client = LlmClient(mode="replay", recordings_dir=tmp_path / "recordings")
    with pytest.raises(ValueError):
        client._path("claude-sonnet-5-5", "sha256:../../etc")
    assert client._path("claude-sonnet-5-5", "sha256:" + "a" * 64).name == "a" * 64 + ".json"
