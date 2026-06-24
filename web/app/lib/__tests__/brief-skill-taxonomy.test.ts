import { describe, expect, it } from "vitest"
import {
  SKILL_TYPE_ACCENTS,
  accentForInsight,
  labelForInsight,
  resolveSkillType,
} from "../brief-skill-taxonomy"
import type { Insight } from "../api"

// Minimal insight stub — only the fields the taxonomy reads.
function ins(partial: Partial<Insight>): Insight {
  return { tag: "something_broken", title: "", subtitle: "", ...partial } as Insight
}

describe("brief-skill-taxonomy", () => {
  it("prefers _card.type over the hoisted type and the legacy tag", () => {
    expect(
      resolveSkillType(ins({ tag: "something_new", type: "growth", _card: { type: "competitive" } })),
    ).toBe("competitive")
  })

  it("falls back to the hoisted top-level type when there is no _card", () => {
    expect(resolveSkillType(ins({ tag: "something_broken", type: "engagement" }))).toBe("engagement")
  })

  it("derives a type from the legacy tag when no skill type is present", () => {
    expect(resolveSkillType(ins({ tag: "something_new" }))).toBe("demand")
    expect(resolveSkillType(ins({ tag: "something_better" }))).toBe("growth")
    expect(resolveSkillType(ins({ tag: "something_broken" }))).toBe("reliability")
  })

  it("ignores an unknown/garbage type and falls back to the tag", () => {
    expect(resolveSkillType(ins({ tag: "something_better", _card: { type: "Mars" } }))).toBe("growth")
  })

  it("derives accent from TYPE, not the card's (possibly-mismatched) accent", () => {
    // The real bug from brief 270: type competitive carried the retention rose.
    const accent = accentForInsight(
      ins({ tag: "something_broken", _card: { type: "competitive", accent: "#b23b52" } }),
    )
    expect(accent).toBe(SKILL_TYPE_ACCENTS.competitive) // #b07a2e ochre, NOT #b23b52
    expect(accent).not.toBe("#b23b52")
  })

  it("labels with the type name only (no priority)", () => {
    expect(labelForInsight(ins({ _card: { type: "compliance" } }))).toBe("Compliance")
  })

  it("every type has an accent and a label", () => {
    for (const t of Object.keys(SKILL_TYPE_ACCENTS)) {
      expect(accentForInsight(ins({ type: t }))).toMatch(/^#[0-9a-f]{6}$/i)
      expect(labelForInsight(ins({ type: t })).length).toBeGreaterThan(0)
    }
  })
})
