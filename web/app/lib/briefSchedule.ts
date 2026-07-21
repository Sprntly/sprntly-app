/**
 * Brief cadence — the pure date math behind Settings → "Communications to you
 * on the Top Product Insights".
 *
 * This is the browser-side mirror of `backend/app/brief_schedule.py`. Both
 * answer the same question ("which local calendar dates does the brief fire
 * on?") with the same rules, so the "Next Brief will land …" preview and the
 * scheduler tick can never disagree. Keep the two in step: `isFireDay` here and
 * `is_fire_day` there are line-for-line equivalents.
 *
 * Everything is pure — `now` is always passed in, never read from the clock —
 * so every cadence, month boundary and DST transition is assertable in a unit
 * test without waiting for a real Monday.
 */

export type BriefFrequency = "daily_weekdays" | "weekly" | "biweekly" | "monthly"

export const BRIEF_FREQUENCIES: { value: BriefFrequency; label: string }[] = [
  { value: "daily_weekdays", label: "Daily (weekdays)" },
  { value: "weekly", label: "Weekly" },
  { value: "biweekly", label: "Every other week" },
  { value: "monthly", label: "Monthly" },
]

/** Weekly is the default: it is the only cadence that existed before this
 *  setting, so an absent/unknown stored value must resolve to it or existing
 *  users' schedules would silently change. */
export const DEFAULT_FREQUENCY: BriefFrequency = "weekly"

/** The "every other week" anchor of last resort — the Unix-epoch Monday.
 *  Deterministic and storage-free, so a company on `biweekly` without a saved
 *  anchor still gets a fixed cadence rather than one that drifts. */
export const DEFAULT_ANCHOR = "1970-01-05"

/**
 * The Day picker offers Monday–Friday only — the brief is a work artefact and
 * a weekend send has no audience. `weekday` therefore lives in 0..4 (0 = Mon).
 *
 * Rows written before that rule can still hold 5 (Sat) or 6 (Sun). Rendering
 * those would leave the `<select>` with a value matching no option (it would
 * silently display Monday while still holding a weekend value), so they are
 * coerced to Monday — the product default, and the next weekday after either
 * weekend day. The backend resolver coerces identically, so a company that
 * never opens this page still moves off the weekend rather than drifting out
 * of sync with what the UI claims.
 */
export const MAX_WEEKDAY = 4 // Friday

export function coerceWeekday(weekday: number): number {
  return Number.isInteger(weekday) && weekday >= 0 && weekday <= MAX_WEEKDAY
    ? weekday
    : 0 // Monday
}

/** The Day dropdown only means something for cadences that pick a weekday.
 *  Daily (weekdays) fires Mon–Fri, so the control is hidden rather than shown
 *  doing nothing. */
export function frequencyUsesDay(frequency: BriefFrequency): boolean {
  return frequency !== "daily_weekdays"
}

/** Monthly means "the FIRST <day> of each month", so the Day dropdown's labels
 *  change from "Mondays" to "First Monday" to make that legible in place. */
export function dayOptionLabel(day: string, frequency: BriefFrequency): string {
  return frequency === "monthly" ? `First ${day}` : `${day}s`
}

export function isBriefFrequency(v: unknown): v is BriefFrequency {
  return (
    v === "daily_weekdays" || v === "weekly" || v === "biweekly" || v === "monthly"
  )
}

/** Read `brief_frequency` out of a `notification_settings` blob, defaulting to
 *  weekly for anything missing or unrecognised. */
export function resolveFrequency(ns: Record<string, unknown> | undefined): BriefFrequency {
  const raw = ns?.brief_frequency
  return isBriefFrequency(raw) ? raw : DEFAULT_FREQUENCY
}

/** Calendar days since the epoch for a local Y/M/D. Pure calendar arithmetic in
 *  UTC space — never a wall-clock instant — so DST cannot shift a date. */
function epochDay(year: number, month: number, day: number): number {
  return Math.floor(Date.UTC(year, month - 1, day) / 86400000)
}

function epochDayFromIso(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso.trim())
  if (!m) return null
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null
  return epochDay(y, mo, d)
}

/** 1970-01-01 was a Thursday; this convention is 0 = Monday. */
function weekdayOf(epochDayNum: number): number {
  return (((epochDayNum + 3) % 7) + 7) % 7
}

/**
 * Does the brief fire on this local calendar date?
 *
 * The single source of truth for cadence, mirroring `is_fire_day` in
 * `backend/app/brief_schedule.py`.
 *
 * `biweekly` floor-divides the anchor offset into whole weeks
 * (`floor(days / 7) % 2 === 0`) rather than testing `days % 14 === 0`, so the
 * alternation survives the user later changing the weekday — which makes the
 * offset from the anchor no longer a clean multiple of 7 — and stays coherent
 * for dates before the anchor.
 */
export function isFireDay(
  epochDayNum: number,
  opts: { weekday: number; frequency: BriefFrequency; anchorDay: number },
): boolean {
  const wd = weekdayOf(epochDayNum)
  if (opts.frequency === "daily_weekdays") return wd <= 4 // Mon..Fri
  if (wd !== opts.weekday) return false
  if (opts.frequency === "biweekly") {
    const weeks = Math.floor((epochDayNum - opts.anchorDay) / 7)
    return (((weeks % 2) + 2) % 2) === 0
  }
  if (opts.frequency === "monthly") {
    // The FIRST matching weekday of the month is the only one in days 1–7.
    return new Date(epochDayNum * 86400000).getUTCDate() <= 7
  }
  return true // weekly
}

/** The local wall-clock date + hour in `tz` for an instant. */
function localNowParts(now: Date, tz: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    hour12: false,
  }).formatToParts(now)
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? NaN)
  // hourCycle h23 can render midnight as "24" in some engines; normalise.
  const hour = get("hour") % 24
  return { year: get("year"), month: get("month"), day: get("day"), hour }
}

/**
 * The epoch-day of the next date the brief fires on, at or after `now`'s local
 * date in `tz`. Null if `tz` is not a usable zone.
 *
 * "Today" only counts when the configured send hour hasn't passed yet in `tz`
 * — matching the backend, which won't fire a cycle whose instant is behind it.
 * The search walks calendar dates (not +24h instants), so DST transitions
 * cannot skip or duplicate a day, and 60 days of headroom comfortably clears
 * the sparsest cadence (monthly's largest gap is 35 days).
 */
export function nextFireDay(
  now: Date,
  tz: string,
  opts: { weekday: number; hour: number; frequency: BriefFrequency; anchor?: string | null },
): number | null {
  let local: ReturnType<typeof localNowParts>
  try {
    local = localNowParts(now, tz)
  } catch {
    return null
  }
  if (!Number.isFinite(local.year) || !Number.isFinite(local.hour)) return null

  const anchorDay =
    (opts.anchor ? epochDayFromIso(opts.anchor) : null) ?? epochDayFromIso(DEFAULT_ANCHOR)!
  const today = epochDay(local.year, local.month, local.day)

  for (let i = 0; i < 60; i++) {
    const cand = today + i
    if (!isFireDay(cand, { weekday: opts.weekday, frequency: opts.frequency, anchorDay })) {
      continue
    }
    // Today's slot already passed → the next one is a later date.
    if (i === 0 && local.hour >= opts.hour) continue
    return cand
  }
  return null
}

export function hourLabel(h: number): string {
  const period = h < 12 ? "AM" : "PM"
  const display = h % 12 === 0 ? 12 : h % 12
  return `${display}:00 ${period}`
}

// Runtimes disagree about UTC's short generic name: measured on Linux, Node
// 20/22/24 all render "GMT+0", while Node 24 on Windows renders "GMT" — same
// ICU major, different answer, so it tracks the platform's ICU build rather
// than any version we could depend on. That variance belongs to whatever
// browser the user opened, not to us, so pin it: collapse the zero-offset
// spellings onto "GMT" and let every other zone pass through untouched.
function normalizeGmt(name: string): string {
  return /^(GMT|UTC)([+-]0+(:00)?)?$/.test(name) ? "GMT" : name
}

/** "America/Los_Angeles" → "PT" (generic short name), cached per zone. */
const tzShortCache = new Map<string, string>()
export function tzShort(tz: string): string {
  let s = tzShortCache.get(tz)
  if (s === undefined) {
    try {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: tz,
        timeZoneName: "shortGeneric",
      }).formatToParts(new Date())
      s = normalizeGmt(parts.find((p) => p.type === "timeZoneName")?.value ?? "")
    } catch {
      s = ""
    }
    tzShortCache.set(tz, s)
  }
  return s
}

/**
 * "Monday, June 1 · 7:00 AM PT" — the next moment the brief will land, in the
 * delivery timezone. Null if the zone is bogus (the preview line simply hides).
 */
export function nextBriefLabel(
  now: Date,
  tz: string,
  opts: { weekday: number; hour: number; frequency: BriefFrequency; anchor?: string | null },
): string | null {
  const day = nextFireDay(now, tz, opts)
  if (day == null) return null
  // Format the resolved calendar date as a plain UTC date — it is a date, not
  // an instant, so rendering it in any other zone could shift it a day.
  const asUtc = new Date(day * 86400000)
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "long",
    month: "long",
    day: "numeric",
  }).formatToParts(asUtc)
  const get = (t: string) => fmt.find((p) => p.type === t)?.value ?? ""
  const short = tzShort(tz)
  return `${get("weekday")}, ${get("month")} ${get("day")} · ${hourLabel(opts.hour)}${
    short ? ` ${short}` : ""
  }`
}

/**
 * The anchor to stamp when a schedule is saved: the ISO date of the first fire
 * under the newly-chosen day, computed as if the cadence were weekly.
 *
 * Anchoring to the first run AFTER the save (rather than to the save instant,
 * or to a fixed global epoch) is what makes "every other week" deterministic
 * and intuitive: the very next brief the user sees is an ON week, and every
 * alternate week from there follows. Re-saving the schedule re-anchors, which
 * is the behaviour a user expects when they change the day.
 */
export function anchorForSave(
  now: Date,
  tz: string,
  opts: { weekday: number; hour: number },
): string {
  const day = nextFireDay(now, tz, { ...opts, frequency: "weekly" })
  if (day == null) return DEFAULT_ANCHOR
  return new Date(day * 86400000).toISOString().slice(0, 10)
}
