from __future__ import annotations

import hashlib

from srg.canonical import canonical_json, digest, pretty_json
from srg.contracts import Note


def test_digest_independent_of_key_order():
    assert digest({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == digest(
        {"a": [1, {"c": 3, "d": 2}], "b": 1}
    )


def test_digest_matches_hand_written_canonical_string():
    obj = {"z": "café", "a": [1, 2], "m": {"y": None, "x": True}}
    hand = '{"a":[1,2],"m":{"x":true,"y":null},"z":"café"}'
    assert canonical_json(obj) == hand
    assert digest(obj) == "sha256:" + hashlib.sha256(hand.encode("utf-8")).hexdigest()


def test_non_ascii_kept():
    assert "é" in canonical_json({"k": "é"})
    assert "\\u" not in pretty_json({"k": "日本"})


def test_models_are_dumped_first():
    note = Note(at="t", author="me", text="hi")
    assert canonical_json(note) == '{"at":"t","author":"me","text":"hi"}'
    assert digest([note]) == digest([{"text": "hi", "author": "me", "at": "t"}])


def test_pretty_json_format():
    assert pretty_json({"b": 1, "a": 2}) == '{\n  "a": 2,\n  "b": 1\n}\n'
