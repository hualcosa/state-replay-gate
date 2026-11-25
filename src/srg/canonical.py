"""Canonical JSON, content digests and the one pretty-printer used for every written file."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic_core import to_jsonable_python


def _jsonable(obj: Any) -> Any:
    """Pydantic models (also nested in lists/dicts) become plain JSON values (mode="json")."""
    return to_jsonable_python(obj)


def canonical_json(obj: Any) -> str:
    """Compact, key-sorted JSON; non-ASCII characters are kept as is."""
    return json.dumps(_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: Any) -> str:
    """``"sha256:"`` + hex digest of ``canonical_json(obj)`` encoded as UTF-8."""
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def pretty_json(obj: Any) -> str:
    """Indented, key-sorted JSON with a trailing newline. Use for every file the code writes."""
    return json.dumps(_jsonable(obj), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
