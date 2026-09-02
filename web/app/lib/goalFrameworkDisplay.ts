/** How a framework's stored/comparison value is said to a reader.
 *
 *  MIRRORS `backend/app/crucible/framework.py`'s `FRAMEWORK_DISPLAY` /
 *  `display_name()` EXACTLY — same "two renderers of one ranking" hazard this
 *  codebase already has for RICE/MoSCoW's arithmetic (see `goalRice.ts` /
 *  `goalMoscow.ts`): a heading that disagreed between the saved document and
 *  the live panel about the SIZE of a finding would be a bug worth failing a
 *  build over, and a heading that disagrees about the framework's NAME is the
 *  same class of bug in smaller print. If this map changes on one side, it
 *  has to change on the other.
 *
 *  `select_framework` only ever stores the lowercase comparison value
 *  ("rice", "moscow") on a real run — never the display casing — so every
 *  render site has to go through this rather than interpolate the stored
 *  value directly. */
const FRAMEWORK_DISPLAY: Record<string, string> = {
  rice: "RICE",
  moscow: "MoSCoW",
  wsjf: "WSJF",
  kano: "Kano",
  "volume-severity": "volume/severity",
  "goal-based": "a goal-based ranking",
}

export function frameworkDisplayName(framework: string): string {
  const key = (framework || "").trim().toLowerCase()
  return FRAMEWORK_DISPLAY[key] ?? framework ?? ""
}
