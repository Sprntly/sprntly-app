// @vitest-environment jsdom
//
// POST /v1/skills answers with two different bodies: the single skill object
// it always has, or `{skills, skipped}` when the uploaded .zip held a folder
// per SKILL.md. Every caller branches on isMultiSkillUpload, so the
// discriminator itself is worth pinning — the two shapes are told apart by the
// `skills` LIST, not by a flag the server could omit.
import { describe, expect, it } from "vitest"

import { isMultiSkillUpload, type SkillUploadResult } from "../api"

const SINGLE: SkillUploadResult = {
  id: "s1",
  slug: "estimation-helper",
  trigger: "/estimation-helper",
  name: "Estimation helper",
  description: "Scores features.",
  uploader_name: "Dana Whitfield",
  created_at: "2026-08-04T10:00:00+00:00",
  has_file: true,
  name_conflict: false,
  replaced: false,
}

describe("isMultiSkillUpload", () => {
  it("is false for the single-skill body", () => {
    expect(isMultiSkillUpload(SINGLE)).toBe(false)
  })

  it("is true for a multi-skill archive, even when every folder was skipped", () => {
    expect(isMultiSkillUpload({ skills: [SINGLE], skipped: [] })).toBe(true)
    expect(
      isMultiSkillUpload({
        skills: [],
        skipped: [{ path: "a", name: "A", reason: "its SKILL.md is empty" }],
      }),
    ).toBe(true)
  })
})
