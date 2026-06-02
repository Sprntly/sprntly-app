import { designAgentApi } from "./api"
import type { PrototypeRecord } from "./api"

export type DesignAgentGenResult =
  | { ok: true; prototype: PrototypeRecord }
  | { ok: false; message: string }

// Structural copy of runPrdGeneration.ts — the 4s tick + 6min cap are
// deliberately duplicated here (and in runEvidenceGeneration.ts) rather than
// factored into a shared helper. Per codebase-agent-patterns.md §"two things
// to NOT do": resist abstracting the poll cadence across runners.
const TICK_MS = 4000
const MAX_MS = 6 * 60 * 1000

/**
 * Polls a prototype that generation has already kicked off (the drawer calls
 * designAgentApi.generate, then hands the id here). Mirrors runPrdGeneration's
 * loop shape. Never throws to the caller — a failed GET is surfaced as
 * { ok: false }.
 */
export async function runDesignAgentGeneration({
  prototypeId,
}: {
  prototypeId: number
}): Promise<DesignAgentGenResult> {
  const startedAt = Date.now()
  let proto: PrototypeRecord
  try {
    proto = await designAgentApi.get(prototypeId)
    while (proto.status === "generating" && Date.now() - startedAt < MAX_MS) {
      await new Promise((r) => setTimeout(r, TICK_MS))
      proto = await designAgentApi.get(prototypeId)
    }
  } catch (err) {
    return {
      ok: false,
      message: err instanceof Error ? err.message : "Request failed",
    }
  }
  if (proto.status === "ready") return { ok: true, prototype: proto }
  if (proto.status === "failed") {
    return { ok: false, message: proto.error || "Generation failed" }
  }
  if (proto.status === "invalidated") {
    return { ok: false, message: "Template invalidated; retry" }
  }
  return { ok: false, message: "Generation timed out (6 minutes)" }
}
