import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import Ajv2020 from "ajv/dist/2020";
import { describe, expect, it } from "vitest";
import { deriveHeadline, deriveScoreboard, deriveT0, heroOf, honestCases, isFixtureData, verifySummary, whereABeatsD } from "../src/derive";
import { formatMoney } from "../src/format";
import { LoadError, loadArtifact, validateArtifact } from "../src/load";
import type { ArmId } from "../src/types";
import { fixtureArtifact } from "./helpers";

const webRoot = resolve(__dirname, "..");
const read = (p: string) => readFileSync(resolve(webRoot, p));

describe("committed copies", () => {
  it("schema copy is byte-identical to ../schemas", () => {
    expect(read("src/generated/run-artifact.schema.json").equals(read("../schemas/run-artifact.schema.json"))).toBe(true);
  });
  it("fixture copy is byte-identical to ../schemas/fixtures", () => {
    expect(read("public/fixtures/run.fixture.json").equals(read("../schemas/fixtures/run.fixture.json"))).toBe(true);
  });
});

describe("schema validation", () => {
  it("fixture validates against the schema with Ajv (Draft 2020-12)", () => {
    const schema = JSON.parse(read("../schemas/run-artifact.schema.json").toString());
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(schema);
    const ok = validate(JSON.parse(read("../schemas/fixtures/run.fixture.json").toString()));
    expect(validate.errors ?? []).toEqual([]);
    expect(ok).toBe(true);
  });
  it("rejects an invalid artifact", () => {
    const bad = fixtureArtifact() as unknown as Record<string, unknown>;
    delete bad.summary;
    expect(validateArtifact(bad).ok).toBe(false);
  });
});

describe("derived figures", () => {
  const a = fixtureArtifact();
  it("scoreboard recomputed from scenarios equals summary.by_arm", () => {
    const board = deriveScoreboard(a);
    const by = a.summary.by_arm as unknown as Record<ArmId, Record<string, unknown>>;
    for (const arm of ["A", "B", "C", "D"] as const) {
      for (const [k, v] of Object.entries(board[arm])) expect(by[arm][k], `${arm}.${k}`).toBe(v);
    }
  });
  it("headline recomputed from scenarios equals summary.regressions", () => {
    expect(deriveHeadline(a)).toEqual(a.summary.regressions);
  });
  it("T0 eval counts match", () => {
    const t0 = deriveT0(a);
    expect(t0.passed).toBe(a.summary.t0_eval_passed);
    expect(t0.total).toBe(a.summary.t0_eval_total);
  });
  it("verifySummary reports nothing for the fixture and flags a tampered summary", () => {
    expect(verifySummary(a)).toEqual([]);
    const t = fixtureArtifact();
    (t.summary.by_arm as unknown as Record<string, { wrong_payments: number }>).C.wrong_payments += 1;
    expect(verifySummary(t).length).toBeGreaterThan(0);
  });
  it("picks the hero by flag and honest cases by kind", () => {
    expect(heroOf(a.scenarios)?.scenario.hero).toBe(true);
    expect(honestCases(a.scenarios).map((s) => s.scenario.kind).sort()).toEqual(["gate_blind_spot", "stale_denial"]);
    const reordered = [...a.scenarios].reverse();
    expect(heroOf(reordered)?.scenario.hero).toBe(true);
  });
  it("finds where arm A beat arm D from data", () => {
    const ids = whereABeatsD(a.scenarios).map((s) => s.scenario.kind);
    expect(ids).toEqual(["stale_denial"]);
  });
});

describe("money", () => {
  it("formats minor units with the currency", () => {
    expect(formatMoney(25000, "USD")).toBe("$250.00");
    expect(formatMoney(7250, "USD")).toBe("$72.50");
    expect(formatMoney(12900, "CAD")).toMatch(/129\.00/);
    expect(formatMoney(500, "JPY")).toMatch(/500/);
  });
});

describe("fixture badge logic", () => {
  it("shows for the fallback source or the fixture model, and not for a real run", () => {
    expect(isFixtureData("fixture-fallback", "gpt-x")).toBe(true);
    expect(isFixtureData("run", "fixture-scripted")).toBe(true);
    expect(isFixtureData("run", "claude-sonnet-5-5")).toBe(false);
  });
});

describe("loadArtifact", () => {
  const fx = JSON.stringify(fixtureArtifact());
  const res = (body: string, status = 200) => Promise.resolve(new Response(body, { status }));
  it("uses data/run.json when it is valid", async () => {
    const f = (async () => res(fx)) as unknown as typeof fetch;
    expect((await loadArtifact(f)).source).toBe("run");
  });
  it("falls back to the fixture on 404", async () => {
    const f = (async (u: string) => (u.includes("data/run.json") ? res("nope", 404) : res(fx))) as unknown as typeof fetch;
    expect((await loadArtifact(f)).source).toBe("fixture-fallback");
  });
  it("falls back when the dev server answers with HTML", async () => {
    const f = (async (u: string) => (u.includes("data/run.json") ? res("<!doctype html>") : res(fx))) as unknown as typeof fetch;
    expect((await loadArtifact(f)).source).toBe("fixture-fallback");
  });
  it("falls back on schema-invalid data and on network failure", async () => {
    const bad = (async (u: string) => (u.includes("data/run.json") ? res("{}") : res(fx))) as unknown as typeof fetch;
    expect((await loadArtifact(bad)).source).toBe("fixture-fallback");
    const net = (async (u: string) => {
      if (u.includes("data/run.json")) throw new TypeError("offline");
      return res(fx);
    }) as unknown as typeof fetch;
    expect((await loadArtifact(net)).source).toBe("fixture-fallback");
  });
  it("throws a LoadError when nothing is usable", async () => {
    const f = (async () => res("{}")) as unknown as typeof fetch;
    await expect(loadArtifact(f)).rejects.toBeInstanceOf(LoadError);
  });
});

import { existsSync } from "node:fs";
describe.skipIf(!existsSync(resolve(__dirname, "../public/data/run.json")))("real run artifact (only when web/public/data/run.json exists)", () => {
  it("validates and its summary recomputes from the scenarios", () => {
    const v = validateArtifact(JSON.parse(read("public/data/run.json").toString()));
    expect(v.ok).toBe(true);
    if (v.ok) expect(verifySummary(v.artifact)).toEqual([]);
  });
  it("shows recorded LLM latency even when gate timings are not included", () => {
    const v = validateArtifact(JSON.parse(read("public/data/run.json").toString()));
    if (!v.ok) throw new Error("invalid");
    const board = deriveScoreboard(v.artifact);
    for (const arm of ["A", "B", "C"] as const) expect(board[arm].llm_latency_ms_p50).not.toBeNull();
  });
});

import { deriveReadout } from "../src/derive";
describe("readout is derived, not asserted", () => {
  it("lists tied arms for fewest wrong payments and never hardcodes a winner", () => {
    const a = fixtureArtifact();
    const r = deriveReadout(a);
    const board = deriveScoreboard(a);
    const min = Math.min(...(["A", "B", "C", "D"] as const).map((i) => board[i].wrong_payments));
    expect(r.fewestWrong.count).toBe(min);
    for (const id of r.fewestWrong.arms) expect(board[id].wrong_payments).toBe(min);
    expect(r.noLlmAtT1).toEqual(["D"]);
    // make D strictly worst: it must not appear as fewest
    for (const s of a.scenarios) { s.arms.D.outcome.classification = "wrong_payment"; s.arms.D.outcome.harm.amount_minor = 99999; }
    expect(deriveReadout(a).fewestWrong.arms).not.toContain("D");
  });
});
