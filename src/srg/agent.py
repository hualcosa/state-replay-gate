"""RefundAgent: turns a DecisionRequest into exactly one tool call via LlmClient (§5.3)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from srg.canonical import pretty_json
from srg.contracts import (
    AgentDecision,
    DecisionRequest,
    DecisionResult,
    LlmMode,
)
from srg.llm import LlmClient

DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_ENV = "SRG_MODEL"
MODE_ENV = "SRG_LLM_MODE"
MAX_TOKENS = 1024
RATIONALE_MAX = 600
MEMORY_NOTE_MAX = 400

_POLICY_PATH = Path(__file__).parent / "prompts" / "policy_v1.md"
POLICY_V1 = _POLICY_PATH.read_text(encoding="utf-8")

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class AgentOutputError(Exception):
    """The model output was not exactly one valid tool call. Never carries the body."""


def _tool(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            },
        },
    }


_COMMON: dict[str, Any] = {
    "order_id": {"type": "string", "description": "The ticket's order_id."},
    "ticket_id": {"type": "string", "description": "The ticket's ticket_id."},
}
_TAIL: dict[str, Any] = {
    "reason": {"type": "string", "description": "Short clause stating the basis for the action."},
    "rationale": {
        "type": "string",
        "description": f"How the policy applies, at most {RATIONALE_MAX} chars.",
    },
    "memory_note": {
        "type": "string",
        "description": (
            f"Note to your future self about this ticket, at most {MEMORY_NOTE_MAX} chars."
        ),
    },
}

TOOLS: list[dict[str, Any]] = [
    _tool(
        "issue_refund",
        "Pay the refund for this ticket.",
        {
            **_COMMON,
            "amount_minor": {
                "type": "integer",
                "minimum": 1,
                "description": "Refund amount in minor units; the ticket's requested_minor.",
            },
            "currency": {
                "type": "string",
                "pattern": "^[A-Z]{3}$",
                "description": "ISO-4217 code; the ticket's currency.",
            },
            **_TAIL,
        },
    ),
    _tool("deny_refund", "Refuse the refund for this ticket.", {**_COMMON, **_TAIL}),
    _tool(
        "escalate_to_human",
        "Hand this ticket to a human approver.",
        {**_COMMON, **_TAIL},
    ),
]
_TOOL_KEYS = {
    t["function"]["name"]: list(t["function"]["parameters"]["properties"]) for t in TOOLS
}


class RefundAgent:
    """Implements DecisionProvider."""

    def __init__(self, *, client: LlmClient, model: str = DEFAULT_MODEL) -> None:
        self.client = client
        self.model = model

    @classmethod
    def from_env(
        cls,
        *,
        mode: LlmMode | None = None,
        recordings_dir: Path,
        model: str | None = None,
    ) -> RefundAgent:
        resolved_mode = mode or os.environ.get(MODE_ENV) or "replay"
        if resolved_mode not in ("replay", "record", "live"):
            raise ValueError(f"{MODE_ENV} must be replay, record or live, got {resolved_mode!r}")
        resolved_model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        client = LlmClient(mode=resolved_mode, recordings_dir=recordings_dir)  # type: ignore[arg-type]
        return cls(client=client, model=resolved_model)

    def latest_recorded_at(self) -> str | None:
        """Max recorded_at over samples served in this process; None in live mode."""
        return self.client.latest_recorded_at

    def build_request(self, req: DecisionRequest) -> dict[str, Any]:
        """Pure and deterministic; uses only ticket, context_kind, state and memory_note."""
        parts = ["Ticket:", pretty_json(req.ticket)]
        if req.context_kind == "state":
            assert req.state is not None
            parts += ["Current account data for the order:", pretty_json(req.state)]
        else:
            parts += [
                "The current account data is not available right now. "
                "This is the note you wrote earlier about this ticket:",
                req.memory_note or "",
            ]
        parts.append("Decide by calling exactly one tool.")
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": POLICY_V1},
                {"role": "user", "content": "\n".join(parts)},
            ],
            "tools": TOOLS,
            "tool_choice": "required",
            "max_tokens": MAX_TOKENS,
        }

    def decide(self, req: DecisionRequest) -> DecisionResult:
        body = self.build_request(req)
        response, ref = self.client.chat(body, sample=req.sample, label=req.label)
        decision = self._parse(response, req, ref.request_hash)
        return DecisionResult(decision=decision, llm=ref)

    @staticmethod
    def _parse(response: dict[str, Any], req: DecisionRequest, request_hash: str) -> AgentDecision:
        where = f"label={req.label} request_hash={request_hash}"

        def fail(why: str) -> AgentOutputError:
            return AgentOutputError(f"{why} ({where})")

        try:
            message = response["choices"][0]["message"]
            calls = message.get("tool_calls") or []
        except (KeyError, IndexError, TypeError, AttributeError):
            raise fail("malformed chat completion") from None
        if not isinstance(calls, list) or len(calls) != 1:
            n = len(calls) if isinstance(calls, list) else "?"
            raise fail(f"expected exactly one tool call, got {n}")
        try:
            fn = calls[0]["function"]
            name = fn["name"]
            raw_args = fn["arguments"]
        except (KeyError, TypeError):
            raise fail("malformed tool call") from None
        if name not in _TOOL_KEYS:
            raise fail("unknown tool name")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else None
        except ValueError:
            args = None
        if not isinstance(args, dict):
            raise fail("tool arguments are not a JSON object")
        missing = [k for k in _TOOL_KEYS[name] if k not in args]
        if missing:
            raise fail(f"missing argument(s): {', '.join(missing)}")
        # the tool schema says additionalProperties: false; enforce it on what comes back
        if len(args) != len(_TOOL_KEYS[name]):
            raise fail(f"unexpected argument(s) for {name}; expected exactly the tool's keys")
        for key in ("order_id", "ticket_id", "reason", "rationale", "memory_note"):
            if not isinstance(args[key], str):
                raise fail(f"argument {key} is not a string")
        if args["order_id"] != req.ticket.order_id or args["ticket_id"] != req.ticket.ticket_id:
            raise fail("order_id/ticket_id do not match the request ticket")
        amount: int | None = None
        currency: str | None = None
        if name == "issue_refund":
            amount, currency = args["amount_minor"], args["currency"]
            if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
                raise fail("amount_minor is not an integer >= 1")
            if not isinstance(currency, str) or not _CURRENCY_RE.match(currency):
                raise fail("currency is not a 3-letter uppercase code")
        try:
            return AgentDecision(
                action=name,
                order_id=args["order_id"],
                ticket_id=args["ticket_id"],
                amount_minor=amount,
                currency=currency,
                reason=args["reason"],
                rationale=args["rationale"][:RATIONALE_MAX],
                memory_note=args["memory_note"][:MEMORY_NOTE_MAX],
            )
        except ValidationError:
            raise fail("decision failed contract validation") from None
