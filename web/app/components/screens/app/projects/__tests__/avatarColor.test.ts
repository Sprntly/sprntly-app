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

  it("the @Sprntly agent avatar glyph is never routed through this helper (source scan)", () => {
    // Static proof: ProjectGroupChat's agent bubble ("aiMark", the plain "s"
    // glyph) never calls personAvatarStyle — only the human av/topAv/
    // memberAv/rosterMember sites do (behavioural proof in
    // ProjectGroupChat.test.tsx / ProjectDetailScreen.test.tsx).
    const src = readFileSync(
      join(__dirname, "../ProjectGroupChat.tsx"),
      "utf8",
    )
    const aiMarkBlock = src.slice(src.indexOf("styles.aiMark"), src.indexOf("styles.aiMark") + 200)
    expect(aiMarkBlock).not.toContain("personAvatarStyle")
  })
})
