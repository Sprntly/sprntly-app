"use client"

// The chat artifact-action footer rows — the two-button insight row and the
// one-button standalone-ticket-set row. Lifted VERBATIM out of `ChatScreen`
// (they used to be co-located exports there) into the shared `chat-shell/`
// home so the main turn mapper and any future consumer import ONE copy — the
// project surfaces render footer content through `ChatBubble.footer` via the
// descriptor's `turnFooter`, so the rows never fork per surface. Props and the
// `bc-*` global class names are unchanged from the pre-move exports.

import { GeneratePrototypeCTA } from "../../design-agent/GeneratePrototypeCTA"

// The chat surface's artifact action row — EXACTLY two buttons. The first opens
// the first available artifact (View Evidence when the insight has evidence, else
// Generate/View PRD); the second is the Generate/View Prototype trigger, disabled
// until a PRD exists (a prototype is always built FROM a PRD). Shared by the
// insight-card row and the reply-footer row so the two never drift.
//
// The prototype button follows BriefChat's pattern: the shared GeneratePrototypeCTA
// with `skipExistenceCheck` (the batch prototype map — chatInsightState — is the
// existence source of truth, so no redundant per-tab getByPrd), driving Generate
// (open the modal) vs View (navigate) from `prototypeReady`.
export function ChatArtifactActions({
  evidenceExists,
  prdExists,
  prdWaiting,
  prdGenerating,
  prdLoading,
  onViewEvidence,
  onOpenPrd,
  prototypePrdId,
  prototypeReady,
  onViewPrototype,
  onPrototypeSettled,
}: {
  evidenceExists: boolean
  prdExists: boolean
  prdWaiting: boolean
  prdGenerating: boolean
  prdLoading?: boolean
  onViewEvidence: () => void
  onOpenPrd: () => void
  prototypePrdId: number | null
  prototypeReady: boolean
  onViewPrototype: () => void
  /** A chat-kicked prototype build finished (success or failure) — the host
   *  posts the artifact chat summary from here. */
  onPrototypeSettled?: (result?: import("../../../lib/runDesignAgentGeneration").DesignAgentGenResult) => void
}) {
  // Order matters: GENERATING (a document is being written) outranks LOADING
  // (one exists and is being fetched), which outranks the settled View/Generate
  // choice. Loading covers both fetching a known PRD and not yet knowing whether
  // one exists — in neither case is anything being authored, so neither may say
  // "Generating".
  const first = evidenceExists
    ? { label: "View Evidence", onClick: onViewEvidence, disabled: false }
    : {
        label: prdGenerating
          ? "Generating PRD…"
          : prdLoading || prdWaiting ? "Loading PRD…"
          : prdExists ? "View PRD" : "Generate PRD",
        onClick: onOpenPrd,
        disabled: prdGenerating || prdLoading || prdWaiting,
      }
  const canPrototype = prototypePrdId != null
  return (
    <div className="bc-actions">
      <button
        type="button"
        className="bc-action-btn bc-action-btn--primary"
        disabled={first.disabled}
        onClick={first.onClick}
      >
        {first.label}
      </button>
      <GeneratePrototypeCTA
        prdId={prototypePrdId}
        skipExistenceCheck
        // Safe: this row shows ONE prototype trigger for the insight's current
        // PRD at a time (mirrors ContentPanel's TicketsBottomBar), so the
        // unscoped da:generating signal can't mislabel a different PRD's run.
        listenForCrossSurfaceGenerating
        onGenerationSettled={onPrototypeSettled}
        render={({ onClick, cta, label }) => (
          <button
            type="button"
            className="bc-action-btn"
            data-testid="chat-prototype-cta"
            disabled={!canPrototype}
            title={canPrototype ? undefined : "Generate a PRD first"}
            onClick={
              cta !== "generating" && canPrototype && prototypeReady
                ? onViewPrototype
                : onClick
            }
          >
            {cta === "generating"
              ? label
              : canPrototype && prototypeReady
                ? "View Prototype"
                : "Generate Prototype"}
          </button>
        )}
      />
    </div>
  )
}

/** The reply-footer action row for a chat whose artifact is a STANDALONE TICKET
 *  SET — one button, not two.
 *
 *  `ChatArtifactActions` above can't serve this: it is hard-wired to an insight
 *  card's evidence/PRD pair, and a chat with no PRD has neither. The prototype
 *  button is deliberately absent rather than disabled — a prototype is built
 *  FROM a PRD, so on this surface it is not a thing the user could enable, and
 *  a permanently-dead button reads as a bug. Same classes as the two-button
 *  row, so the two never drift visually. */
export function ChatTicketSetActions({
  state,
  onClick,
}: {
  /** running → the run owns the button; failed → it offers the re-run; ready
   *  (and any settled state with a set behind it) → it reopens the panel. */
  state: "running" | "ready" | "failed"
  onClick: () => void
}) {
  const label =
    state === "running" ? "Writing tickets…"
    : state === "failed" ? "Retry tickets"
    : "View Tickets"
  return (
    <div className="bc-actions">
      <button
        type="button"
        className="bc-action-btn bc-action-btn--primary"
        data-testid="chat-ticket-set-cta"
        disabled={state === "running"}
        onClick={onClick}
      >
        {label}
      </button>
    </div>
  )
}
