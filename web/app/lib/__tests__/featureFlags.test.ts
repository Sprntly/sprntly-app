import { afterEach, describe, expect, it, vi } from "vitest"
import { projectsEnabled } from "../featureFlags"

describe("projectsEnabled", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it.each(["1", "true", "YES", " true "])(
    "returns true for truthy value %j",
    (value) => {
      vi.stubEnv("NEXT_PUBLIC_PROJECTS_ENABLED", value)
      expect(projectsEnabled()).toBe(true)
    }
  )

  it("returns false when unset", () => {
    vi.stubEnv("NEXT_PUBLIC_PROJECTS_ENABLED", "")
    expect(projectsEnabled()).toBe(false)
  })

  it.each(["", "0", "false", "no"])(
    "returns false for falsy value %j",
    (value) => {
      vi.stubEnv("NEXT_PUBLIC_PROJECTS_ENABLED", value)
      expect(projectsEnabled()).toBe(false)
    }
  )
})
