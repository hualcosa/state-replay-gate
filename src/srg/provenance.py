"""Recording provenance check: ``srg verify FILE --recordings DIR`` (review round 3, finding 3).

``verify_artifact`` only checks that an artifact is internally consistent. This module checks
that every LLM call in it comes from a recording: for each call it rebuilds the request with the
agent's pure ``build_request`` (from the artifact's scenario, the recomputed S1 and T0's memory
note), checks that the request hashes to the stored ``request_hash``, replays that recording and
sample offline, and checks that the parsed decision and call reference equal the artifact's.
Replay only: a missing recording is reported, never fetched.
"""

from __future__ import annotations

from pathlib import Path

from srg.canonical import digest
from srg.contracts import (
    AgentDecision,
    DecisionRequest,
    LlmCallRef,
    RecordingMissError,
    RunArtifact,
    WorldState,
)


def verify_recordings(artifact: RunArtifact, recordings_dir: Path) -> list[str]:
    """Return one problem per LLM call that does not match the recordings in ``recordings_dir``."""
    from srg.agent import AgentOutputError, RefundAgent
    from srg.arms import arm_a_sample
    from srg.llm import LlmClient
    from srg.state import apply_patch

    recordings_dir = Path(recordings_dir)
    if not recordings_dir.is_dir():
        return [f"recordings directory not found: {recordings_dir}"]
    agent = RefundAgent(
        client=LlmClient(mode="replay", recordings_dir=recordings_dir), model=artifact.run.model
    )
    problems: list[str] = []
    for run in artifact.scenarios:
        sc = run.scenario
        s0 = sc.t0.state
        try:
            s1 = apply_patch(s0, sc.mutation.ops)
        except ValueError as exc:
            problems.append(f"{sc.id}: mutation does not apply: {exc}")
            continue
        note = run.t0.decision.memory_note
        calls: list[tuple[str, LlmCallRef | None, AgentDecision, WorldState | None, int]] = [
            ("t0", run.t0.llm, run.t0.decision, s0, 0),
            ("C", run.arms["C"].llm, run.arms["C"].decision, s0, 1),
            ("A", run.arms["A"].llm, run.arms["A"].decision, s1, arm_a_sample(s0, s1)),
            ("B", run.arms["B"].llm, run.arms["B"].decision, None, 0),
        ]
        for arm, llm, decision, state, sample in calls:
            where = f"{sc.id}/{arm}"
            if llm is None:
                problems.append(f"{where}: no llm call to check against the recordings")
                continue
            req = DecisionRequest(
                label=where,
                ticket=sc.ticket,
                context_kind="state" if state is not None else "memory_note",
                state=state,
                memory_note=None if state is not None else note,
                sample=sample,
            )
            rebuilt = digest(agent.build_request(req))
            if rebuilt != llm.request_hash:
                problems.append(
                    f"{where}: request_hash {llm.request_hash} is not the hash of the rebuilt "
                    f"request {rebuilt}"
                )
                continue
            try:
                result = agent.decide(req)
            except RecordingMissError:
                problems.append(f"{where}: no recording for {rebuilt} sample {sample}")
                continue
            except AgentOutputError as exc:
                problems.append(f"{where}: recorded response does not parse: {exc}")
                continue
            if result.llm != llm:
                problems.append(f"{where}: llm call reference differs from the recording")
            if result.decision != decision:
                fields = [
                    f
                    for f in AgentDecision.model_fields
                    if getattr(result.decision, f) != getattr(decision, f)
                ]
                problems.append(
                    f"{where}: decision differs from the recorded response ({', '.join(fields)})"
                )
    return problems
