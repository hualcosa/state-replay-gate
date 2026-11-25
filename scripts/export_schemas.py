"""Export JSON Schemas (Draft 2020-12) from the pydantic contracts into schemas/.

Usage: uv run python scripts/export_schemas.py [--check]
--check exits 1 if a committed schema differs from a fresh export.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from srg.canonical import pretty_json
from srg.contracts import Recording, RunArtifact, Scenario

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schemas"
DRAFT = "https://json-schema.org/draft/2020-12/schema"

TARGETS: list[tuple[str, type[BaseModel], Literal["validation", "serialization"]]] = [
    ("run-artifact.schema.json", RunArtifact, "serialization"),
    ("scenario.schema.json", Scenario, "validation"),
    ("recording.schema.json", Recording, "validation"),
]


def build(model: type[BaseModel], mode: Literal["validation", "serialization"]) -> dict[str, Any]:
    return {"$schema": DRAFT, **model.model_json_schema(mode=mode)}


def rendered() -> dict[str, str]:
    return {name: pretty_json(build(model, mode)) for name, model, mode in TARGETS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed files are stale")
    args = parser.parse_args(argv)
    stale = []
    for name, text in rendered().items():
        path = SCHEMAS_DIR / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    if stale:
        print(f"stale schemas (run scripts/export_schemas.py): {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
