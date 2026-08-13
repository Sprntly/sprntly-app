import type { AppContentState, PrdState } from "../types/content"

/** `"{briefId}:{insightIndex}"`, or null when either half is absent. The insight
 *  key is the ONLY provenance a PRD and an evidence document share (evidence
 *  rows carry an `evidence_id` and never a `prd_id` — see AppContentState's
 *  `evidence` doc comment). */
export function insightKeyOf(
  m: { briefId?: number | null; insightIndex?: number | null } | null | undefined,
): string | null {
  if (m == null) return null
  if (m.briefId == null || m.insightIndex == null) return null
  return `${m.briefId}:${m.insightIndex}`
}

/** Slices of shared content the scope decision reads. */
type ScopeContent = Pick<
  AppContentState,
  "prd" | "prdMeta" | "detail" | "evidence" | "ticketSet"
>

/**
 * The PRD a content-panel control may ACT on (mint a share for, generate a
 * prototype from, name in the header), given the artifact the panel is
 * DISPLAYING. Returns null when the cached PRD cannot be shown to own what is
 * on screen — controls read this, never `content.prd` directly.
 */
export function prdInScopeFor(
  content: ScopeContent,
  activeTab: "evidence" | "prd" | "tickets" | "reports" | "document",
): PrdState | null {
  const { prd } = content
  if (prd == null) return null
  // Neither Reports nor Document is part of the PRD pipeline: both hang off
  // the CHAT THREAD, so no PRD is actionable while one of them is open.
  // Listing them explicitly rather than defaulting means a future tab has to
  // make this choice deliberately instead of inheriting "PRD applies".
  if (activeTab === "reports" || activeTab === "document") return null
  // A STANDALONE ticket set is on screen: these tickets came out of a chat with
  // no PRD behind them, so whatever PRD is still sitting in the shared slot did
  // not produce them. Left in scope it would arm the Share menu, the PDF export
  // and the prototype CTA on a document the reader is not looking at — the
  // exact failure `evidenceOpenScopePatch` was written to prevent, by the other
  // route into it.
  if (activeTab === "tickets" && content.ticketSet != null) return null
  if (activeTab === "prd" || activeTab === "tickets") return prd

  // activeTab === "evidence"
  // Precedence deliberately MIRRORS EvidenceTab's own loader precedence
  // (ContentPanel.tsx's detail.meta effect vs. its prdMeta effect):
  // detail.meta wins when both are set.
  const displayedKey = insightKeyOf(content.detail?.meta) ?? insightKeyOf(content.prdMeta)

  if (displayedKey == null) {
    // An evidence document is on screen whose provenance we cannot
    // establish → FAIL CLOSED. No evidence document → the tab is showing
    // its own empty state and content.prd is the only artifact in scope
    // → allow (this branch exists so the resume-a-PRD flow, which sets
    //   `prd` with `prdMeta: null`, does not lose a legitimate Share).
    return content.evidence ? null : prd
  }

  return insightKeyOf(prd) === displayedKey ? prd : null
}

/**
 * The patch every path that opens an evidence document it cannot attribute to
 * the cached PRD must apply, so no PRD-acting control is left armed on a
 * document the reader has navigated away from.
 */
export function evidenceOpenScopePatch(): Pick<
  AppContentState,
  "prd" | "prdMeta" | "prdGenerating" | "prdPartialHtml" | "detail"
> {
  return {
    // The panel is global: opening this evidence document must not leave another
    // PRD's document in the shared slot for the header/Share/prototype controls
    // to act on. Same contract the chat-side evidence-first open already applies.
    prd: null,
    prdMeta: null,
    prdGenerating: false,
    prdPartialHtml: null,
    detail: null,
  }
}

/**
 * The patch EVERY path that opens a standalone ticket set must apply — the chat
 * kick-off, a thread resume, the Artifacts library's open-by-id.
 *
 * A standalone set has no PRD and no evidence brief behind it by definition, so
 * anything left in those slots belongs to whatever the reader was looking at
 * BEFORE. `prdInScopeFor` already refuses to hand the cached PRD to a tickets
 * tab showing a set, but the slots feed more than that one decision: they also
 * decide which TABS the panel advertises, and a stale PRD/evidence pair would
 * put a PRD and an Evidence tab on a chat that has neither, each opening
 * another thread's document.
 */
export function ticketSetOpenScopePatch(): Pick<
  AppContentState,
  | "prd" | "prdMeta" | "prdGenerating" | "prdPartialHtml" | "detail"
  | "evidence" | "evidenceId" | "evidenceGenerating" | "evidencePartialHtml"
> {
  return {
    prd: null,
    prdMeta: null,
    prdGenerating: false,
    prdPartialHtml: null,
    detail: null,
    evidence: null,
    evidenceId: null,
    evidenceGenerating: false,
    evidencePartialHtml: null,
  }
}
