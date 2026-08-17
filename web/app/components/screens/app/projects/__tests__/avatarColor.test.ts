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

  it("the @Sprntly agent avatar is never routed through this helper (source scan) — RETARGETED to the engine post-fold", () => {
    // Post-fold the pre-fold host's `styles.aiMark` scan went VACUOUS (the thin
    // host has no aiMark → indexOf(-1) → empty slice → passes proving nothing).
    // The agent avatar now renders bubble-less through ChatBubble; the group
    // engine (`useProjectGroupThread`) is where a turn's `avatarStyle` is
    // assigned, and it deliberately sets an AGENT turn's `avatarStyle` to
    // `undefined` (never personAvatarStyle) — only human self/peer turns get a
    // per-person tint. Assert that guard exists where the logic now lives.
    const engineSrc = readFileSync(join(__dirname, "../useProjectGroupThread.ts"), "utf8")
    expect(engineSrc).toContain("avatarStyle: isAgent ? undefined : personAvatarStyle(")
    // Non-vacuous negative: the aiMark scan is gone (the host is thin) — the
    // agent avatar is not colour-keyed anywhere in the host.
    const hostSrc = readFileSync(join(__dirname, "../ProjectGroupChat.tsx"), "utf8")
    expect(hostSrc).not.toContain("styles.aiMark")
  })
})
