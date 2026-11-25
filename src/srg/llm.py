"""Hash-keyed LLM recorder: offline exact replay, proxy recording, and live calls (§5.4)."""

from __future__ import annotations

import contextlib
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from srg.canonical import digest, pretty_json
from srg.contracts import (
    SCHEMA_VERSION,
    LlmCallRef,
    LlmMode,
    RecordedSample,
    Recording,
    RecordingMissError,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8317"
BASE_URL_ENV = "CLIPROXY_BASE_URL"
API_KEY_ENV = "CLIPROXY_API_KEY"
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
_HEX_RE = re.compile(r"[0-9a-f]{64}")

__all__ = [
    "LlmClient",
    "LlmConfigError",
    "LlmTransportError",
    "RecordingMissError",
    "RecordingSequenceError",
]


class LlmConfigError(Exception):
    """Missing configuration (for example the API key variable)."""


class LlmTransportError(Exception):
    """The proxy call failed. Carries the status and label only, never a body or header."""


class RecordingSequenceError(ValueError):
    """record mode asked for a sample beyond the next free index."""


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usage(response: dict[str, Any], key: str) -> int | None:
    usage = response.get("usage")
    if isinstance(usage, dict):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


class LlmClient:
    def __init__(
        self,
        *,
        mode: LlmMode,
        recordings_dir: Path,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if mode not in ("replay", "record", "live"):
            raise ValueError(f"unknown LLM mode {mode!r}")
        self.mode: LlmMode = mode
        self.recordings_dir = Path(recordings_dir)
        self.base_url = (base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self.timeout_s = timeout_s
        self._transport = transport
        self._latest_recorded_at: str | None = None

    # -- public ---------------------------------------------------------------------------

    @property
    def latest_recorded_at(self) -> str | None:
        """Max ``recorded_at`` over recorded samples served by this client (None in live)."""
        return self._latest_recorded_at

    def chat(
        self, request: dict[str, Any], *, sample: int = 0, label: str
    ) -> tuple[dict[str, Any], LlmCallRef]:
        if sample < 0:
            raise ValueError(f"sample must be >= 0, got {sample} (label={label})")
        request_hash = digest(request)
        model = str(request.get("model", ""))
        if self.mode == "live":
            return self._call(request, request_hash, sample, label)

        path = self._path(model, request_hash)
        existing = self._load(path) if path.exists() else None

        if self.mode == "replay":
            if (
                existing is None
                or existing.request != request
                or sample >= len(existing.samples)
            ):
                raise RecordingMissError(label, request_hash, sample)
            return self._serve(existing, sample, model)

        # record
        if existing is not None and existing.request != request:
            raise RecordingSequenceError(
                f"recording {path.name} does not match its request (label={label})"
            )
        if existing is not None and sample < len(existing.samples):
            self._add_label(existing, label, path)
            return self._serve(existing, sample, model)
        have = len(existing.samples) if existing is not None else 0
        if sample > have:
            raise RecordingSequenceError(
                f"label={label} sample={sample} but only {have} sample(s) recorded; "
                "record samples in order"
            )
        response, ref = self._call(request, request_hash, sample, label)
        recorded_at = _utc_now()
        new_sample = RecordedSample(
            sample=sample, response=response, latency_ms=ref.latency_ms, recorded_at=recorded_at
        )
        if existing is None:
            recording = Recording(
                schema_version=SCHEMA_VERSION,  # type: ignore[arg-type]
                request_hash=request_hash,
                labels=[label],
                request=request,
                samples=[new_sample],
            )
        else:
            recording = Recording(
                schema_version=SCHEMA_VERSION,  # type: ignore[arg-type]
                request_hash=request_hash,
                labels=sorted({*existing.labels, label}),
                request=request,
                samples=[*existing.samples, new_sample],
            )
        self._write(path, recording)
        self._note_recorded_at(recorded_at)
        return response, ref

    # -- internals ------------------------------------------------------------------------

    def _path(self, model: str, request_hash: str) -> Path:
        # model and hash come from requests or artifacts, so neither may escape recordings_dir.
        hex_digest = request_hash.removeprefix("sha256:")
        if not _MODEL_RE.fullmatch(model) or ".." in model:
            raise ValueError(f"invalid model id for a recording path: {model!r}")
        if not _HEX_RE.fullmatch(hex_digest):
            raise ValueError(f"invalid request hash for a recording path: {request_hash!r}")
        root = self.recordings_dir.resolve()
        path = (root / model / f"{hex_digest}.json").resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"recording path escapes {root}: {path}")
        return path

    @staticmethod
    def _load(path: Path) -> Recording:
        return Recording.model_validate_json(path.read_text(encoding="utf-8"))

    def _serve(
        self, recording: Recording, sample: int, model: str
    ) -> tuple[dict[str, Any], LlmCallRef]:
        item = recording.samples[sample]
        self._note_recorded_at(item.recorded_at)
        ref = LlmCallRef(
            request_hash=recording.request_hash,
            sample=sample,
            model=model,
            latency_ms=item.latency_ms,
            prompt_tokens=_usage(item.response, "prompt_tokens"),
            completion_tokens=_usage(item.response, "completion_tokens"),
        )
        return item.response, ref

    def _note_recorded_at(self, value: str) -> None:
        if self._latest_recorded_at is None or value > self._latest_recorded_at:
            self._latest_recorded_at = value

    def _add_label(self, recording: Recording, label: str, path: Path) -> None:
        if label in recording.labels:
            return
        updated = recording.model_copy(update={"labels": sorted({*recording.labels, label})})
        self._write(path, updated)

    @staticmethod
    def _write(path: Path, recording: Recording) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            tmp.write_text(pretty_json(recording), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()

    def _key(self) -> str:
        key = self._api_key or os.environ.get(API_KEY_ENV)
        if not key:
            raise LlmConfigError(f"{API_KEY_ENV} is not set; it is required in record/live mode")
        return key

    def _call(
        self, request: dict[str, Any], request_hash: str, sample: int, label: str
    ) -> tuple[dict[str, Any], LlmCallRef]:
        key = self._key()
        url = f"{self.base_url}/v1/chat/completions"
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
                resp = client.post(
                    url,
                    json=request,
                    headers={"Authorization": f"Bearer {key}"},
                )
        except httpx.HTTPError as exc:
            raise LlmTransportError(
                f"request failed (label={label}): {type(exc).__name__}"
            ) from None
        latency_ms = int((time.perf_counter() - started) * 1000)
        if not 200 <= resp.status_code < 300:
            raise LlmTransportError(f"proxy returned status {resp.status_code} (label={label})")
        try:
            body = resp.json()
        except ValueError:
            raise LlmTransportError(
                f"proxy returned non-JSON body, status {resp.status_code} (label={label})"
            ) from None
        if not isinstance(body, dict):
            raise LlmTransportError(f"proxy returned a non-object body (label={label})")
        ref = LlmCallRef(
            request_hash=request_hash,
            sample=sample,
            model=str(request.get("model", "")),
            latency_ms=latency_ms,
            prompt_tokens=_usage(body, "prompt_tokens"),
            completion_tokens=_usage(body, "completion_tokens"),
        )
        return body, ref
