// The consumer decision for a `member.added` signal → the navigation INTENT:
// which project (if any) to land the just-added user in. Pure function, so the
// intent is testable without the realtime subscription or the router.
import { describe, expect, it } from "vitest"
import { memberAddedLandingTarget } from "../memberAddedLanding"

const base = { currentProjectId: 7, alreadyInPrivateChat: false, busy: false }

describe("memberAddedLandingTarget", () => {
  it("returns the signalled project id for a different project the user isn't viewing", () => {
    expect(
      memberAddedLandingTarget({ project_id: 42, kind: "added" }, base),
    ).toBe(42)
  })

  it("returns the same project id when the user is on it but NOT in the private chat", () => {
    // On the group tab of project 7, added → land them in 7's private chat.
    expect(
      memberAddedLandingTarget({ project_id: 7 }, { ...base, alreadyInPrivateChat: false }),
    ).toBe(7)
  })

  it("does nothing when already sitting in this project's private chat", () => {
    expect(
      memberAddedLandingTarget({ project_id: 7 }, { ...base, alreadyInPrivateChat: true }),
    ).toBeNull()
  })

  it("still lands the user in a DIFFERENT project even while in a private chat", () => {
    expect(
      memberAddedLandingTarget({ project_id: 99 }, { ...base, alreadyInPrivateChat: true }),
    ).toBe(99)
  })

  it("never yanks a user who is actively typing", () => {
    expect(
      memberAddedLandingTarget({ project_id: 42 }, { ...base, busy: true }),
    ).toBeNull()
  })

  it("ignores a malformed or non-member.added payload", () => {
    expect(memberAddedLandingTarget(null, base)).toBeNull()
    expect(memberAddedLandingTarget({}, base)).toBeNull()
    expect(memberAddedLandingTarget({ project_id: "42" }, base)).toBeNull()
    expect(memberAddedLandingTarget("member.added", base)).toBeNull()
  })
})
