import { describe, expect, it } from "vitest"

import { chatStamp } from "../recentChats"

// The stamp exists because a conversation's title is the first message
// verbatim: ask the same thing twice and the nav shows two identical rows.
// These pin the shapes it has to produce for that to help.
describe("chatStamp", () => {
  const now = new Date("2026-08-27T15:30:00Z")

  it("reads as a clock time for something asked today", () => {
    // Three asks of the same question within an hour are only distinguishable
    // by the minute.
    const stamp = chatStamp("2026-08-27T11:56:00Z", now)
    expect(stamp).toMatch(/^\d{1,2}:\d{2}$/)
  })

  it("keeps the clock on a past day, or it separates nothing", () => {
    // The failure this replaced: the four rows it exists to tell apart were
    // all asked on ONE day, 45 minutes apart. A "2d" stamp made all four
    // identical — the exact problem it was added to solve.
    const a = chatStamp("2026-08-25T11:19:00Z", now)
    const b = chatStamp("2026-08-25T11:56:00Z", now)
    const c = chatStamp("2026-08-25T12:04:00Z", now)
    expect(new Set([a, b, c]).size).toBe(3)
    for (const stamp of [a, b, c]) expect(stamp).toMatch(/^25 Aug, \d{1,2}:\d{2}$/)
  })

  it("says nothing at all for an unparseable date", () => {
    // Better a row with no stamp than one reading "NaN".
    expect(chatStamp("not-a-date", now)).toBe("")
    expect(chatStamp("", now)).toBe("")
  })
})
