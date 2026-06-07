/**
 * Client + catalog for the four AI coworkers (design-v4 onboarding page 07).
 *
 * Four specialists join every workspace. The user names each one; the name
 * is how the coworker signs its work in chats, briefs, and comments. The
 * backend owns the slot map (backend/app/coworkers.py): pm / pd / ds / admin.
 */
import { api } from "../api"

export type CoworkerSlot = "pm" | "pd" | "ds" | "admin"

export type CoworkerNames = Record<CoworkerSlot, string>

export type CoworkerMeta = {
  slot: CoworkerSlot
  /** "Product coworker", etc. */
  label: string
  /** One-line description of what the coworker does. */
  blurb: string
  /** Placeholder handle shown in the name field (matches the v4 mock). */
  placeholder: string
  /** Agent-color family from the design system (see §7 of the guide). */
  color: "pm" | "pd" | "ds" | "admin"
}

/** Display order matches the page-07 mock: Product, Design, Data Science, Admin. */
export const COWORKERS: CoworkerMeta[] = [
  {
    slot: "pm",
    label: "Product coworker",
    blurb: "Weekly briefs, prioritization, PRDs",
    placeholder: "name_pm",
    color: "pm",
  },
  {
    slot: "pd",
    label: "Design coworker",
    blurb: "Prototypes from PRDs, design crit",
    placeholder: "name_pd",
    color: "pd",
  },
  {
    slot: "ds",
    label: "Data Science coworker",
    blurb: "Cohorts, root-cause analysis, charts",
    placeholder: "name_ds",
    color: "ds",
  },
  {
    slot: "admin",
    label: "Admin coworker",
    blurb: "Keeps artifacts in sync — applies edits once the team aligns in comments",
    placeholder: "name_admin",
    color: "admin",
  },
]

export const COWORKER_SLOTS: CoworkerSlot[] = COWORKERS.map((c) => c.slot)

export function emptyCoworkerNames(): CoworkerNames {
  return { pm: "", pd: "", ds: "", admin: "" }
}

/** Trim every slot; drops surrounding whitespace before persisting. */
export function normalizeCoworkerNames(names: CoworkerNames): CoworkerNames {
  return {
    pm: names.pm.trim(),
    pd: names.pd.trim(),
    ds: names.ds.trim(),
    admin: names.admin.trim(),
  }
}

/** All four coworkers must be named before the workspace can launch. */
export function canLaunchWorkspace(names: CoworkerNames): boolean {
  return COWORKER_SLOTS.every((slot) => names[slot].trim().length > 0)
}

export const coworkersApi = {
  get: () =>
    api
      .get<CoworkerNames>("/v1/company/coworkers")
      .catch(() => emptyCoworkerNames()),
  put: (names: CoworkerNames) =>
    api.put<{ ok: true; coworker_names: CoworkerNames }>(
      "/v1/company/coworkers",
      normalizeCoworkerNames(names),
    ),
}
