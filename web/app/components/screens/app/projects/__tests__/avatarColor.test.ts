// avatarColor.ts — the sole per-person avatar-color source. Deterministic +
// stable-per-key, empty-key → {}, and id-preferred-over-name.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"
import { personAvatarStyle } from "../avatarColor"

describe("personAvatarStyle — determinism + stability (AC-9)", () => {
  it("test_personAvatarStyle_deterministic_stable — same key returns the same style across repeated calls", () => {
    const a = personAvatarStyle("u1", "Ada")
    const b = personAvatarStyle("u1", "Ada")
    const c = personAvatarStyle("u1", "Ada")
    expect(a).toEqual(b)
    expect(b).toEqual(c)
    expect(a.background).toBeTruthy()
    expect(a.color).toBeTruthy()
  })

  it("different keys are not all forced to the same tint (palette actually varies)", () => {
    const styles = ["u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10"].map((id) =>
      personAvatarStyle(id, id),
    )
    const distinctBackgrounds = new Set(styles.map((s) => s.background))
    expect(distinctBackgrounds.size).toBeGreaterThan(1)
  })

  it("test_personAvatarStyle_empty_key_returns_empty — no id/name → {}", () => {
    expect(personAvatarStyle(null, null)).toEqual({})
    expect(personAvatarStyle(undefined, undefined)).toEqual({})
    expect(personAvatarStyle("", "")).toEqual({})
    expect(personAvatarStyle("   ", "   ")).toEqual({})
  })

  it("test_personAvatarStyle_prefers_user_id — the id drives the tint, name is only the fallback", () => {
    // Same id, different names → same tint (id wins).
    const withName1 = personAvatarStyle("u1", "Ada")
    const withName2 = personAvatarStyle("u1", "Someone Else Entirely")
    expect(withName1).toEqual(withName2)

    // No id, name-only → falls back to name (may differ from the id-driven tint).
    const nameOnly = personAvatarStyle(null, "u1")
    expect(nameOnly.background).toBeTruthy()
  })

  it("the @Sprntly agent avatar is never routed through this helper (source scan) — RETARGETED to the shared conversation engine", () => {
    // The per-surface group engine (`useProjectGroupThread` / `ProjectGroupChat`)
    // was DELETED and folded into the shared `useProjectConversation`. That is
    // now where a group turn's `avatarStyle` is assigned. The exclusion holds by
    // structure: an ASSISTANT (agent) turn returns EARLY with only `{ query,
    // reply }` — no `author`, no `avatarStyle` — while `personAvatarStyle` is
    // called exactly once, on the HUMAN peer branch keyed off `author_user_id`.
    // So the agent avatar can never receive a per-person tint.
    const engineSrc = readFileSync(join(__dirname, "../useProjectConversation.ts"), "utf8")

    // The single person-tint call is keyed to a HUMAN author.
    const tintCalls = engineSrc.match(/personAvatarStyle\(/g) ?? []
    expect(tintCalls).toHaveLength(1)
    expect(engineSrc).toContain("personAvatarStyle(gt.author_user_id, gt.author_name)")

    // Non-vacuous negative: the agent/assistant turn short-circuits BEFORE the
    // person-tint call — the exclusion is a structural early-return, not an
    // afterthought. (If the assistant branch were removed or moved below the
    // tint call, this ordering assertion fails.)
    const assistantIdx = engineSrc.indexOf('gt.role === "assistant"')
    const tintIdx = engineSrc.indexOf("personAvatarStyle(gt.author_user_id")
    expect(assistantIdx).toBeGreaterThan(-1)
    expect(tintIdx).toBeGreaterThan(-1)
    expect(assistantIdx).toBeLessThan(tintIdx)
  })
})
