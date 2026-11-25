"""In-memory world-state store: JSON Patch (add/replace/remove), leaf diff, logged reader."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from srg.canonical import digest
from srg.contracts import FieldChange, PatchOp, StateRead, WorldState

__all__ = [
    "FieldChange",
    "StateRead",
    "StateReader",
    "apply_patch",
    "diff_states",
    "escape_pointer_token",
    "parse_pointer",
]


def escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def parse_pointer(pointer: str) -> list[str]:
    """RFC 6901 pointer to tokens. ``""`` is the whole document. Raises ValueError if malformed."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must be empty or start with '/': {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _list_index(token: str, length: int, *, allow_end: bool, pointer: str) -> int:
    if token == "-" and allow_end:
        return length
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise ValueError(f"invalid list index {token!r} in {pointer!r}")
    index = int(token)
    limit = length if allow_end else length - 1
    if index > limit:
        raise ValueError(f"list index {index} out of range in {pointer!r}")
    return index


def _parent(doc: Any, tokens: list[str], pointer: str) -> Any:
    node = doc
    for token in tokens[:-1]:
        if isinstance(node, dict):
            if token not in node:
                raise ValueError(f"path not found: {pointer!r}")
            node = node[token]
        elif isinstance(node, list):
            node = node[_list_index(token, len(node), allow_end=False, pointer=pointer)]
        else:
            raise ValueError(f"cannot descend into a scalar at {pointer!r}")
    return node


def _apply_op(doc: Any, op: PatchOp) -> None:
    tokens = parse_pointer(op.path)
    if not tokens:
        raise ValueError("patching the document root is not supported")
    parent = _parent(doc, tokens, op.path)
    last = tokens[-1]
    value = copy.deepcopy(op.value)
    if isinstance(parent, dict):
        if op.op == "add":
            parent[last] = value
            return
        if last not in parent:
            raise ValueError(f"path not found: {op.path!r}")
        if op.op == "replace":
            parent[last] = value
        else:
            del parent[last]
    elif isinstance(parent, list):
        index = _list_index(last, len(parent), allow_end=op.op == "add", pointer=op.path)
        if op.op == "add":
            parent.insert(index, value)
        elif op.op == "replace":
            parent[index] = value
        else:
            del parent[index]
    else:
        raise ValueError(f"cannot patch inside a scalar at {op.path!r}")


def apply_patch(state: WorldState, ops: Sequence[PatchOp]) -> WorldState:
    """Pure: returns a new, re-validated WorldState; ``state`` is never modified.

    Raises ValueError on a bad pointer and pydantic ValidationError if the result is invalid.
    """
    doc = state.model_dump(mode="json")
    for op in ops:
        _apply_op(doc, op)
    return WorldState.model_validate(doc)


def _diff(before: Any, after: Any, path: str, out: list[FieldChange]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        keys = list(before) + [k for k in after if k not in before]
        for key in keys:
            sub = f"{path}/{escape_pointer_token(key)}"
            if key not in after:
                out.append(FieldChange(path=sub, before=before[key], after=None))
            elif key not in before:
                out.append(FieldChange(path=sub, before=None, after=after[key]))
            else:
                _diff(before[key], after[key], sub, out)
    elif isinstance(before, list) and isinstance(after, list):
        for i in range(max(len(before), len(after))):
            sub = f"{path}/{i}"
            if i >= len(after):
                out.append(FieldChange(path=sub, before=before[i], after=None))
            elif i >= len(before):
                out.append(FieldChange(path=sub, before=None, after=after[i]))
            else:
                _diff(before[i], after[i], sub, out)
    elif before != after or type(before) is not type(after):
        out.append(FieldChange(path=path, before=before, after=after))


def diff_states(before: WorldState, after: WorldState) -> list[FieldChange]:
    """Leaf-level changes in document order (C6)."""
    out: list[FieldChange] = []
    _diff(before.model_dump(mode="json"), after.model_dump(mode="json"), "", out)
    return out


class StateReader:
    """Read-only view of one state. Every ``read`` is logged in call order."""

    def __init__(self, state: WorldState, at: str) -> None:
        self._doc: dict[str, Any] = state.model_dump(mode="json")
        self._digest = digest(self._doc)
        self.at = at
        self.reads: list[StateRead] = []

    @property
    def state_digest(self) -> str:
        """Digest of the whole state; not logged as a read."""
        return self._digest

    def read(self, path: str) -> Any:
        """Deep copy of the value at a JSON Pointer (``""`` = whole doc). Missing → KeyError."""
        node: Any = self._doc
        for token in parse_pointer(path):
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
                node = node[int(token)]
            else:
                raise KeyError(path)
        self.reads.append(StateRead(path=path, value=copy.deepcopy(node)))
        return copy.deepcopy(node)
