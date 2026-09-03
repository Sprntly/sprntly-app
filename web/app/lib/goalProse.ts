/** Cleanups applied to model-authored prose at the render boundary, mirroring
 *  `backend/app/crucible/report.py`'s `strip_claim_refs` / `_stop` /
 *  `_upper_first`.
 *
 *  APPLIED IN THE RENDERER, NOT IN THE ENGINE, for the same reason the lint
 *  runs there: the stored recommendation stays exactly what the model
 *  returned, and each renderer cleans independently, so neither can be the one
 *  that forgot. */

/** An inline claim-id reference in model-authored prose: `[<uuid>]`, usually
 *  several in a row. The deep pass is asked to cite, and it cites INLINE as
 *  well as in the `changes[]` structure the citation gate reads — so a
 *  sentence reaches the page as "…on procurement grounds alone
 *  [16e40304-1113-5253-b624-f300317b5fdd][189ac9b0-0aec-52b0-8069-a16f542c19bc].
 *  Second, …". Nothing stripped them; the only reason a reader had not seen
 *  one is that truncation happened to cut before they appeared.
 *
 *  MATCHED ON THE UUID SHAPE, NOT ON BRACKETS, so a legitimate bracketed aside
 *  or a `[sic]` survives. Leading whitespace goes with the reference, so
 *  removing a mid-sentence citation does not strand the space before a full
 *  stop. The citations are NOT lost: every accepted change renders its claim's
 *  own assertion text beside it, which is the provenance a reader can use. */
const CLAIM_REF =
  /\s*\[[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\]/g

export function stripClaimRefs(text: string): string {
  return (text || "").replace(CLAIM_REF, "")
}

//: Sentence-final punctuation. A string already ending in one of these does
//: not get another appended.
const TERMINALS = ".!?…"

/** `text` with exactly one closing full stop. Several sentences here are built
 *  as "lead-in {value}." where `value` may already be a complete sentence —
 *  `cannot_answer`'s `because` carries the framework reason, which ends in its
 *  own full stop, so the page rendered "…what it only asks for..". */
export function stop(text: string): string {
  const t = (text || "").trimEnd()
  if (!t) return t
  return TERMINALS.includes(t[t.length - 1]) ? t : `${t}.`
}

/** `text` with its first letter capitalised and nothing else touched.
 *
 *  NOT `toUpperCase()` on the whole thing and not a title-caser: several of
 *  these strings are written as CLAUSES because they also render mid-sentence
 *  elsewhere. Where one follows a bold full stop it has to start a sentence,
 *  and "How many got a full recommendation. you named a target of…" is the
 *  result of leaving it alone. */
export function upperFirst(text: string): string {
  const t = (text || "").trimStart()
  return t ? t[0].toUpperCase() + t.slice(1) : t
}
