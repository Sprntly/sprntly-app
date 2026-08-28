// Pure node-env test for the invite flow's email-needle helper — no jsdom,
// no React.
import { describe, expect, it } from "vitest"
import { isEmailNeedle } from "../mentions"

describe("isEmailNeedle", () => {
  it("recognises bare emails and rejects names", () => {
    expect(isEmailNeedle("jane@acme.com")).toBe(true)
    expect(isEmailNeedle("Fortune")).toBe(false)
    expect(isEmailNeedle("@Fortune")).toBe(false)
  })
})
