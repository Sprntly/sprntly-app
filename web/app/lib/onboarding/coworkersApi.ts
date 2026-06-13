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

/** Sensible default names so coworker naming is OPTIONAL: any slot the user
 *  leaves blank is named for them on launch. */
export const DEFAULT_COWORKER_NAMES: CoworkerNames = {
  pm: "Atlas",
  pd: "Juno",
  ds: "Vera",
  admin: "Ada",
}

/**
 * Display-only handle preview for the page-07 pill (e.g. "Maya" → "maya_pm").
 *
 * The backend stores plain names (companies.coworker_names) and derives no
 * handle of its own, so this is purely presentational: lowercase the typed
 * name, strip everything but [a-z0-9], and append the slot suffix — mirrors
 * the mock's cwUpdate() (base + "_" + slot), with "name" as the empty base so
 * the untouched pill reads name_pm / name_pd / … like the design.
 */
export function coworkerHandle(slot: CoworkerSlot, name: string): string {
  const base = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "") || "name"
  return `${base}_${slot}`
}

/** Fill any empty/whitespace slot with its default name. */
export function withCoworkerDefaults(names: CoworkerNames): CoworkerNames {
  const out = { ...names }
  for (const slot of COWORKER_SLOTS) {
    if (!out[slot] || !out[slot].trim()) out[slot] = DEFAULT_COWORKER_NAMES[slot]
  }
  return out
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
