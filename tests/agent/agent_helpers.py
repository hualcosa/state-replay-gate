"""Helpers for agent/recorder tests. No network: every client uses a MockTransport."""

from __future__ import annotations

import json

import httpx

from srg.contracts import DecisionRequest, Scenario

KEY = "dummy"


def make_request(scenario: Scenario, *, label: str = "F01/t0", sample: int = 0) -> DecisionRequest:
    ticket = next(t for t in scenario.t0.state.tickets if t.ticket_id == scenario.ticket_id)
    return DecisionRequest(
        label=label,
        ticket=ticket,
        context_kind="state",
        state=scenario.t0.state,
        memory_note=None,
        sample=sample,
    )


def tool_response(name: str, args: dict, *, prompt: int = 100, completion: int = 20) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def refund_args(ticket, **over) -> dict:
    args = {
        "order_id": ticket.order_id,
        "ticket_id": ticket.ticket_id,
        "amount_minor": ticket.requested_minor,
        "currency": ticket.currency,
        "reason": "return in transit, within window",
        "rationale": "All rules hold.",
        "memory_note": "Approved.",
    }
    args.update(over)
    return args


class FakeProxy:
    """MockTransport handler that returns queued bodies and records requests."""

    def __init__(self, responses: list[dict] | None = None, status: int = 200) -> None:
        self.responses = list(responses or [])
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="boom " + KEY)
        body = self.responses.pop(0)
        return httpx.Response(200, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"network touched: {request.url}")


NO_NETWORK = httpx.MockTransport(no_network)
