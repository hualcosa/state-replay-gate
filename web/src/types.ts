import type {
  ArmRun,
  ArmSpec,
  Classification,
  Disposition,
  RunArtifact as GeneratedRunArtifact,
  ScenarioRun as GeneratedScenarioRun,
  WorldState,
} from "./generated/run-artifact";

export type { ArmRun, ArmSpec, Classification, Disposition, WorldState };

export type ArmId = "A" | "B" | "C" | "D";
export const ARM_IDS: readonly ArmId[] = ["A", "B", "C", "D"] as const;

/** The generated interface keys arm dicts by string; the schema restricts them to A-D. */
export type ScenarioRun = Omit<GeneratedScenarioRun, "arms"> & {
  arms: Record<ArmId, ArmRun>;
};
export type RunArtifact = Omit<GeneratedRunArtifact, "scenarios"> & {
  scenarios: ScenarioRun[];
};

export type DataSource = "run" | "fixture-fallback";
/** Why the page fell back to the fixture: no data file, a file that failed validation, a fetch error, or a render error. */
export type FallbackReason = "missing" | "invalid" | "unreachable" | "render";
export interface LoadedArtifact {
  artifact: RunArtifact;
  source: DataSource;
  reason?: FallbackReason;
  /** Diagnostic details for the fallback, newest last. */
  notes?: string[];
}
