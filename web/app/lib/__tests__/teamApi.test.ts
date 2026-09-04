// Pure-function coverage for the bulk-invite parsers in lib/teamApi.ts.
//
// Both were lifted verbatim out of the retired onboarding InviteStep
// (2026-09-03) when its bulk paste + CSV import moved to Settings → Team &
// roles — see TeamSettings.bulkInvite.test.tsx for the mount-level behavior
// (wiring the parsed rows through teamApi.invite).
import { describe, expect, it } from "vitest"

import { JOB_ROLE_OPTIONS } from "../onboarding/types"
import { parseInvitesCsv, parsePastedEmails } from "../teamApi"

describe("parseInvitesCsv — teammate CSV import", () => {
  it("parses email, job role and permission per line", () => {
    expect(
      parseInvitesCsv("a@acme.com,Engineer,admin\nb@acme.com,Designer,viewer"),
    ).toEqual([
      { email: "a@acme.com", jobRole: "Engineer", permission: "admin" },
      { email: "b@acme.com", jobRole: "Designer", permission: "viewer" },
    ])
  })

  it("skips a header row whose first cell is 'email'", () => {
    expect(parseInvitesCsv("email,role,permission\na@acme.com,Engineer,admin")).toEqual([
      { email: "a@acme.com", jobRole: "Engineer", permission: "admin" },
    ])
  })

  it("dedupes repeated emails (case-insensitively) and lowercases them", () => {
    expect(
      parseInvitesCsv("a@acme.com,Engineer,admin\nA@Acme.com,Designer,viewer"),
    ).toEqual([{ email: "a@acme.com", jobRole: "Engineer", permission: "admin" }])
  })

  it("drops malformed emails and blank lines", () => {
    expect(parseInvitesCsv("not-an-email,Engineer\n\n ,x\nok@acme.com")).toEqual([
      { email: "ok@acme.com", jobRole: JOB_ROLE_OPTIONS[0], permission: "member" },
    ])
  })

  it("defaults a missing job role to the first option and an unknown permission to member", () => {
    expect(parseInvitesCsv("a@acme.com")).toEqual([
      { email: "a@acme.com", jobRole: JOB_ROLE_OPTIONS[0], permission: "member" },
    ])
    expect(parseInvitesCsv("b@acme.com,Marketing,owner")).toEqual([
      { email: "b@acme.com", jobRole: "Marketing", permission: "member" },
    ])
  })
})

describe("parsePastedEmails — the bulk paste field", () => {
  it("splits on commas, semicolons, newlines and stray whitespace", () => {
    const rows = parsePastedEmails(
      "a@acme.com, b@acme.com;c@acme.com\nd@acme.com  e@acme.com",
    )
    expect(rows.map((r) => r.email)).toEqual([
      "a@acme.com",
      "b@acme.com",
      "c@acme.com",
      "d@acme.com",
      "e@acme.com",
    ])
    // Pasted rows default to the first job role + member permission.
    expect(rows[0].permission).toBe("member")
  })

  it("drops malformed addresses, self-duplicates, and ones already listed", () => {
    const rows = parsePastedEmails(
      "GOOD@acme.com, not-an-email, good@acme.com, dupe@acme.com",
      [{ email: "Dupe@acme.com", jobRole: "Engineer", permission: "admin" }],
    )
    expect(rows.map((r) => r.email)).toEqual(["good@acme.com"])
  })
})
