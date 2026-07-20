import { designAgentApi } from "./api"
import { sleepUntilNextPoll } from "./poll"
import type { PrototypeRecord } from "./api"

export type DesignAgentGenResult =
  | { ok: true; prototype: PrototypeRecord }
  | { ok: false; message: string; timedOut?: true }

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
      // Visibility-aware sleep (shared poll.ts): a backgrounded tab throttles
      // setTimeout to ~1/min, stalling polling though the server-side job
      // finishes. Refocusing wakes immediately and re-reads the real status.
      // The TICK_MS/MAX_MS cadence stays local per the runner-cadence note.
      await sleepUntilNextPoll(TICK_MS)
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
  // Client-side give-up only, not a genuine backend failure: the poll loop
  // exits on local elapsed time without knowing whether the backend job is
  // still running (and it routinely still is — see the Site 1/2/3 callers of
  // this discriminant). Message text stays byte-identical to before.
  return {
    ok: false,
    timedOut: true,
    message: "Generation timed out (6 minutes)",
  }
}
