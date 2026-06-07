// Pure-function tests for the onboarding required-field validation helper.
// No DOM — the helper turns a list of field checks into an error map, the
// first invalid key (for focus), and an ok flag. Shared by the v4
// onboarding steps via useFieldValidation in InterviewLayout.
import { describe, expect, it } from "vitest"
import {
  requireText,
  validateRequired,
  type FieldCheck,
} from "../onboarding/validation"

describe("validateRequired", () => {
  it("returns ok with no errors when every check passes", () => {
    const checks: FieldCheck[] = [
      { key: "a", valid: true, message: "A required" },
      { key: "b", valid: true, message: "B required" },
    ]
    const result = validateRequired(checks)
    expect(result.ok).toBe(true)
    expect(result.errors).toEqual({})
    expect(result.firstInvalid).toBeNull()
  })

  it("collects a message per invalid field", () => {
    const result = validateRequired([
      { key: "a", valid: false, message: "A required" },
      { key: "b", valid: true, message: "B required" },
      { key: "c", valid: false, message: "C required" },
    ])
    expect(result.ok).toBe(false)
    expect(result.errors).toEqual({ a: "A required", c: "C required" })
  })

  it("reports the first invalid field in declared order (for focus)", () => {
    const result = validateRequired([
      { key: "first", valid: true, message: "x" },
      { key: "second", valid: false, message: "y" },
      { key: "third", valid: false, message: "z" },
    ])
    expect(result.firstInvalid).toBe("second")
  })

  it("treats an empty check list as valid", () => {
    expect(validateRequired([]).ok).toBe(true)
  })
})

describe("requireText", () => {
  it("is valid for non-empty trimmed text", () => {
    expect(requireText("name", "  Acme  ", "msg").valid).toBe(true)
  })

  it("is invalid for empty or whitespace-only text", () => {
    expect(requireText("name", "", "msg").valid).toBe(false)
    expect(requireText("name", "   ", "msg").valid).toBe(false)
  })

  it("carries through the key and message", () => {
    const c = requireText("company", "", "Enter your company name.")
    expect(c.key).toBe("company")
    expect(c.message).toBe("Enter your company name.")
  })
})
