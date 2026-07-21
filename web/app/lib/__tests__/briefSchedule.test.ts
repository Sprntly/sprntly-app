// Brief cadence date math — the "Next Brief will land …" preview.
//
// This is the highest-risk part of the frequency feature: the preview has to
// agree with the backend scheduler for four cadences across month boundaries,
// DST transitions and the biweekly anchor. Everything here is pure (`now` is
// injected), so each case is a fixed instant in, a fixed string out.
import { describe, expect, it } from "vitest"

import {
  anchorForSave,
  dayOptionLabel,
  frequencyUsesDay,
  coerceWeekday,
  isFireDay,
  nextBriefLabel,
  nextFireDay,
  resolveFrequency,
  tzShort,
} from "../briefSchedule"

/** ISO date of the next fire day — the assertable core, free of tz-label noise. */
function nextDate(
  nowIso: string,
  tz: string,
  opts: { weekday: number; hour: number; frequency: never | string; anchor?: string },
): string | null {
  const d = nextFireDay(new Date(nowIso), tz, opts as never)
  return d == null ? null : new Date(d * 86400000).toISOString().slice(0, 10)
}

describe("isFireDay", () => {
  // 2026-07-20 is a Monday.
  const mon = Math.floor(Date.UTC(2026, 6, 20) / 86400000)

  it("daily_weekdays fires Mon–Fri and never on the weekend", () => {
    const fires = Array.from({ length: 7 }, (_, i) =>
      isFireDay(mon + i, { weekday: 0, frequency: "daily_weekdays", anchorDay: 0 }),
    )
    expect(fires).toEqual([true, true, true, true, true, false, false])
  })

  it("weekly fires only on the chosen weekday", () => {
    const fires = Array.from({ length: 7 }, (_, i) =>
      isFireDay(mon + i, { weekday: 2, frequency: "weekly", anchorDay: 0 }),
    )
    // 0 = Monday, so index 2 is Wednesday.
    expect(fires).toEqual([false, false, true, false, false, false, false])
  })

  it("biweekly alternates weeks around its anchor, in both directions", () => {
    const at = (weeks: number) =>
      isFireDay(mon + weeks * 7, { weekday: 0, frequency: "biweekly", anchorDay: mon })
    expect([at(-2), at(-1), at(0), at(1), at(2), at(3)]).toEqual([
      true, false, true, false, true, false,
    ])
  })

  it("monthly fires only on the FIRST matching weekday of the month", () => {
    // August 2026: Mondays fall on the 3rd, 10th, 17th, 24th, 31st.
    const day = (d: number) => Math.floor(Date.UTC(2026, 7, d) / 86400000)
    const opts = { weekday: 0, frequency: "monthly" as const, anchorDay: 0 }
    expect(isFireDay(day(3), opts)).toBe(true)
    expect(isFireDay(day(10), opts)).toBe(false)
    expect(isFireDay(day(31), opts)).toBe(false)
  })
})

describe("nextFireDay — weekly (unchanged legacy behaviour)", () => {
  it("picks today when the send hour is still ahead", () => {
    // Mon 2026-07-20 04:00 UTC, send at 06:00 → today.
    expect(nextDate("2026-07-20T04:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "weekly" }))
      .toBe("2026-07-20")
  })

  it("rolls to next week once today's send hour has passed", () => {
    expect(nextDate("2026-07-20T06:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "weekly" }))
      .toBe("2026-07-27")
  })

  it("resolves the date in the DELIVERY timezone, not the browser's", () => {
    // 2026-07-20T03:00Z is still Sunday 20:00 in Los Angeles, so the next
    // Monday-06:00 slot is LA's Monday the 20th.
    expect(
      nextDate("2026-07-20T03:00:00Z", "America/Los_Angeles", {
        weekday: 0, hour: 6, frequency: "weekly",
      }),
    ).toBe("2026-07-20")
  })
})

describe("nextFireDay — daily (weekdays)", () => {
  it("advances one day at a time inside the week", () => {
    expect(
      nextDate("2026-07-21T09:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "daily_weekdays" }),
    ).toBe("2026-07-22") // Tue 09:00 → Wed
  })

  it("jumps Friday's send straight to Monday, skipping the weekend", () => {
    // Fri 2026-07-24 09:00 UTC (send hour passed) → Mon 2026-07-27.
    expect(
      nextDate("2026-07-24T09:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "daily_weekdays" }),
    ).toBe("2026-07-27")
  })

  it("ignores the configured weekday entirely", () => {
    const asSunday = nextDate("2026-07-21T09:00:00Z", "UTC", {
      weekday: 6, hour: 6, frequency: "daily_weekdays",
    })
    expect(asSunday).toBe("2026-07-22")
  })
})

describe("nextFireDay — every other week", () => {
  it("fires on the anchor week, then skips the next", () => {
    const anchor = "2026-07-20" // a Monday
    // Just before the anchor's send time → the anchor day itself.
    expect(
      nextDate("2026-07-20T04:00:00Z", "UTC", {
        weekday: 0, hour: 6, frequency: "biweekly", anchor,
      }),
    ).toBe("2026-07-20")
    // Just after it → skip 07-27, land on 08-03.
    expect(
      nextDate("2026-07-20T06:00:00Z", "UTC", {
        weekday: 0, hour: 6, frequency: "biweekly", anchor,
      }),
    ).toBe("2026-08-03")
  })

  it("keeps a 14-day cadence across a month boundary", () => {
    expect(
      nextDate("2026-08-03T06:00:00Z", "UTC", {
        weekday: 0, hour: 6, frequency: "biweekly", anchor: "2026-07-20",
      }),
    ).toBe("2026-08-17")
  })

  it("still alternates when the weekday is changed after anchoring", () => {
    // Anchor is a Monday; the user switches to Thursdays. Floor-dividing into
    // whole weeks keeps a clean 14-day cadence off the anchor's week.
    const anchor = "2026-07-20"
    const first = nextDate("2026-07-20T00:00:00Z", "UTC", {
      weekday: 3, hour: 6, frequency: "biweekly", anchor,
    })
    expect(first).toBe("2026-07-23") // Thu of the anchor week
    const second = nextDate("2026-07-23T06:00:00Z", "UTC", {
      weekday: 3, hour: 6, frequency: "biweekly", anchor,
    })
    expect(second).toBe("2026-08-06") // 14 days later, not 7
  })
})

describe("nextFireDay — monthly (first <day> of the month)", () => {
  it("picks the first matching weekday of the coming month", () => {
    // After Aug's first Monday (the 3rd) → Sept's first Monday (the 7th).
    expect(
      nextDate("2026-08-03T06:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "monthly" }),
    ).toBe("2026-09-07")
  })

  it("handles a month that STARTS on the chosen weekday", () => {
    // 2027-02-01 is a Monday, so it is February's first Monday.
    expect(
      nextDate("2027-01-15T06:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "monthly" }),
    ).toBe("2027-02-01")
  })

  it("handles a month whose first matching weekday is the 7th (the boundary)", () => {
    // 2026-08-01 is a Saturday, so August's first Friday is the 7th — the
    // latest a "first <weekday>" can ever fall, and the edge the day<=7 rule
    // has to include.
    expect(
      nextDate("2026-07-15T06:00:00Z", "UTC", { weekday: 4, hour: 6, frequency: "monthly" }),
    ).toBe("2026-08-07")
  })

  it("crosses a year boundary", () => {
    // After Dec 2026's first Monday (the 7th) → Jan 2027's (the 4th).
    expect(
      nextDate("2026-12-07T06:00:00Z", "UTC", { weekday: 0, hour: 6, frequency: "monthly" }),
    ).toBe("2027-01-04")
  })
})

describe("DST", () => {
  it("keeps the same local send DATE across a spring-forward transition", () => {
    // US DST starts Sun 2026-03-08. The Monday after is 2026-03-09 — a naive
    // +24h-per-step walk can slip a day here; the calendar walk cannot.
    expect(
      nextDate("2026-03-06T12:00:00Z", "America/New_York", {
        weekday: 0, hour: 6, frequency: "weekly",
      }),
    ).toBe("2026-03-09")
  })

  it("keeps the same local send DATE across a fall-back transition", () => {
    // US DST ends Sun 2026-11-01; the Monday after is 2026-11-02.
    expect(
      nextDate("2026-10-30T12:00:00Z", "America/New_York", {
        weekday: 0, hour: 6, frequency: "weekly",
      }),
    ).toBe("2026-11-02")
  })

  it("holds the configured wall-clock hour across the transition", () => {
    const label = nextBriefLabel(new Date("2026-03-06T12:00:00Z"), "America/New_York", {
      weekday: 0, hour: 6, frequency: "weekly",
    })
    expect(label).toContain("Monday, March 9")
    expect(label).toContain("6:00 AM")
  })
})

describe("nextBriefLabel", () => {
  it("renders the full preview line", () => {
    expect(
      nextBriefLabel(new Date("2026-07-20T04:00:00Z"), "UTC", {
        weekday: 0, hour: 7, frequency: "weekly",
      }),
      // UTC's short generic name normalizes to "GMT" — see tzShort, which
      // pins this across ICU versions that would otherwise say "GMT+0".
    ).toBe("Monday, July 20 · 7:00 AM GMT")
  })

  it("returns null for a bogus timezone so the line hides", () => {
    expect(
      nextBriefLabel(new Date("2026-07-20T04:00:00Z"), "Not/AZone", {
        weekday: 0, hour: 7, frequency: "weekly",
      }),
    ).toBeNull()
  })
})

describe("tzShort", () => {
  // UTC's shortGeneric is "GMT+0" on Linux (Node 20/22/24 alike) and "GMT" on
  // Windows, so CI and a Windows dev machine genuinely disagreed on the label.
  // tzShort normalizes it; this pins the normalized spelling on both.
  it("renders UTC as GMT regardless of the runtime's ICU version", () => {
    expect(tzShort("UTC")).toBe("GMT")
  })

  it("keeps a real offset zone's suffix", () => {
    // Whatever ICU calls it, a non-zero offset must not collapse to "GMT".
    expect(tzShort("Etc/GMT+5")).not.toBe("GMT")
  })

  it("leaves named zones untouched", () => {
    expect(tzShort("America/Los_Angeles")).toBe("PT")
  })

  it("returns an empty string for a bogus zone", () => {
    expect(tzShort("Not/AZone")).toBe("")
  })
})

describe("anchorForSave", () => {
  it("anchors to the first run AFTER the save, not the save instant", () => {
    // Saved Tue 2026-07-21 with Mondays selected → the next Monday.
    expect(
      anchorForSave(new Date("2026-07-21T12:00:00Z"), "UTC", { weekday: 0, hour: 6 }),
    ).toBe("2026-07-27")
  })

  it("makes the very next brief an ON week for biweekly", () => {
    const now = new Date("2026-07-21T12:00:00Z")
    const anchor = anchorForSave(now, "UTC", { weekday: 0, hour: 6 })
    expect(
      nextDate("2026-07-21T12:00:00Z", "UTC", {
        weekday: 0, hour: 6, frequency: "biweekly", anchor,
      }),
    ).toBe(anchor)
  })
})

describe("weekday-only send days", () => {
  it("passes Mon–Fri through untouched", () => {
    expect([0, 1, 2, 3, 4].map(coerceWeekday)).toEqual([0, 1, 2, 3, 4])
  })

  it("coerces legacy Saturday/Sunday values to Monday", () => {
    // The Day picker no longer offers a weekend, so a stored 5/6 would leave
    // the <select> displaying an option it does not hold.
    expect(coerceWeekday(5)).toBe(0)
    expect(coerceWeekday(6)).toBe(0)
  })

  it("coerces junk to Monday rather than producing an unrenderable day", () => {
    expect(coerceWeekday(-1)).toBe(0)
    expect(coerceWeekday(99)).toBe(0)
    expect(coerceWeekday(1.5)).toBe(0)
    expect(coerceWeekday(NaN)).toBe(0)
  })

  it("can never land the preview on a weekend for any cadence", () => {
    // Exhaustive: every offerable weekday × every cadence, from a fixed now.
    const now = new Date("2026-07-21T12:00:00Z")
    for (const frequency of ["weekly", "biweekly", "monthly", "daily_weekdays"]) {
      for (const weekday of [0, 1, 2, 3, 4]) {
        const day = nextFireDay(now, "UTC", {
          weekday, hour: 6, frequency: frequency as never, anchor: "2026-07-20",
        })
        expect(day).not.toBeNull()
        // 1970-01-01 was a Thursday ⇒ 0 = Monday under this convention.
        const wd = (((day! + 3) % 7) + 7) % 7
        expect(wd).toBeLessThanOrEqual(4)
      }
    }
  })
})

describe("settings helpers", () => {
  it("hides the Day dropdown only for daily (weekdays)", () => {
    expect(frequencyUsesDay("daily_weekdays")).toBe(false)
    expect(frequencyUsesDay("weekly")).toBe(true)
    expect(frequencyUsesDay("biweekly")).toBe(true)
    expect(frequencyUsesDay("monthly")).toBe(true)
  })

  it("relabels the Day options for monthly", () => {
    expect(dayOptionLabel("Monday", "weekly")).toBe("Mondays")
    expect(dayOptionLabel("Monday", "monthly")).toBe("First Monday")
  })

  it("defaults absent/unknown stored frequencies to weekly", () => {
    expect(resolveFrequency(undefined)).toBe("weekly")
    expect(resolveFrequency({})).toBe("weekly")
    expect(resolveFrequency({ brief_frequency: "nonsense" })).toBe("weekly")
    expect(resolveFrequency({ brief_frequency: "monthly" })).toBe("monthly")
  })
})
