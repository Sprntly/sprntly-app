/** The sentence a person reads when a team document could not be written.
 *
 *  ONE MAP, TWO SURFACES. The panel (DocumentTab) and the full page
 *  (DocumentRoute) both render a failed document, and they used to say the same
 *  hard-coded sentence for every failure — which meant a generation that came
 *  back empty, one the model refused and one a deploy interrupted were
 *  indistinguishable to the person who asked for it. They are different
 *  situations with different next steps, and the only one that matters to a
 *  reader is "will asking again help?".
 *
 *  The COPY lives here rather than in the API because it is copy: the server
 *  owns the meaning (`error_code`, a closed set — see
 *  custom_artifact_generate.FAILURE_CODES) and the client owns the words. That
 *  split is also what keeps the raw server error out of the product — it is
 *  exception text, and this library is shared with the whole team.
 */

/** Codes the backend writes. Anything else — including null, which is every
 *  document that failed before the column existed — takes the unknown case. */
const COPY: Record<string, string> = {
  empty: "The model returned an empty document. Asking again usually works.",
  llm_error: "The document generator could not be reached. Ask for it again in chat.",
  too_large: "The document came back too long to store. Ask for a shorter one.",
  // WRITTEN, then not kept — the opposite fact from `llm_error`, so it must not
  // borrow that sentence. Saying "the generator could not be reached" about a
  // generation that plainly succeeded is the same confident falsehood this
  // whole map exists to stop.
  storage_error: "The document was written but could not be saved. Ask for it again in chat.",
  interrupted: "Writing was interrupted by a server restart. Ask for it again in chat.",
}

const UNKNOWN = "This document could not be written. Ask for it again in chat."

export function documentFailureCopy(code: string | null | undefined): string {
  // An unrecognised code is treated as unknown rather than rendered raw. The
  // server and the web deploy separately, so a code this bundle has never heard
  // of is a NORMAL state during a rollout — printing it verbatim would put an
  // identifier in front of a user, and the generic sentence is still true.
  //
  // `Object.hasOwn`, not a bare `COPY[code]`: the code is an arbitrary string
  // off the wire, and a plain object literal inherits `constructor`,
  // `toString`, `valueOf` and friends. Those lookups return FUNCTIONS, which
  // are truthy, so `documentFailureCopy("constructor")` would satisfy this
  // function's `: string` type at compile time and hand React a function to
  // render — a blank notice, on the one surface whose entire job is to not be
  // blank.
  return code && Object.hasOwn(COPY, code) ? COPY[code] : UNKNOWN
}
