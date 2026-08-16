"use client"

/**
 * "Share this PRD on my slack channel and ask the team for feedback" — the
 * card that shows what will be posted, and where, BEFORE anything is.
 *
 * A message in a team channel is public and cannot be taken back, so the chat
 * never posts on the strength of its own reading of a sentence. The planner
 * decides that a share was asked for; `POST /v1/share/slack/preview` resolves
 * the document and the channel and composes the message; and this card is
 * where a person looks at both and says yes.
 *
 * THE PREVIEW IS SPLIT IN TWO, deliberately. The note is the user's own words
 * and stays editable right up to the moment they send; the document block
 * underneath — kind, title, teaser, link — is what Sprntly asserts and is not
 * editable here, because it is read from the database at send time and the
 * card must not imply otherwise. Rendering the fixed half from `target` +
 * `summary` rather than echoing the server's composed `text` is what keeps the
 * preview honest while the note is being typed.
 *
 * Testability split mirrors ClarifyQuestionsCard: this file is pure markup +
 * local UI state, with no I/O — the host owns both network calls.
 */

import { useState } from "react"
import { IconSparkle } from "./app-icons"
import type { SlackSharePreview, SlackShareTarget } from "../../lib/api"

/** How a settled share ended — the card keeps its shape and becomes a record,
 *  rather than vanishing and leaving the thread claiming nothing happened.
 *   - "sent": posted; `channelName` is where.
 *   - "cancelled": the user declined. Nothing was posted, and the card says so
 *     explicitly, because an ambiguous outcome here is the failure mode this
 *     whole flow exists to avoid.
 *   - "failed": Slack rejected it; `error` is the reason it gave. */
export type SlackShareResolution =
  | { outcome: "sent"; channelName: string }
  | { outcome: "cancelled" }
  | { outcome: "failed"; error: string }

export type SlackSharePreviewCardProps = {
  preview: SlackSharePreview
  /** True while the send is in flight — the card goes inert rather than
   *  unmounting mid-click, and the button says what is happening. */
  busy?: boolean
  /** Set once the share is settled: the card stops taking input and becomes a
   *  read-only record of what was decided. */
  resolved?: SlackShareResolution
  /** Post it. `note` is whatever the user last had in the box — the only
   *  caller-supplied text that reaches Slack. */
  onSend: (channelId: string, note: string) => void
  /** Decline. Nothing is posted, and the card records that. */
  onCancel: () => void
  /** Choose which document, when the phrase matched several. Only reached by
   *  a host that renders the choice itself; the chat routes it through the
   *  QuestionPopup instead — see `questionInPopup`. */
  onPickTarget?: (target: SlackShareTarget) => void
  /** The host is asking the open question (channel, or which document) in the
   *  QuestionPopup above the composer, so this card must NOT render a picker
   *  of its own.
   *
   *  Owner's directive, 2026-08-16: anything that asks the user to choose goes
   *  through the popup, the way the clarify gate and ticket assignment already
   *  do. The first cut put a row of channel chips inside the card — a second
   *  question surface, in a product that had settled on one. The card keeps
   *  showing the MESSAGE (choosing a destination for something you cannot see
   *  is not a choice) and says where the question is. */
  questionInPopup?: boolean
}

/** The document half of the message — fixed, not editable. Rendered from the
 *  resolved target so it stays accurate while the note is being typed. */
function DocumentBlock({
  target,
  summary,
}: {
  target: SlackShareTarget
  summary: string
}) {
  return (
    <div className="ssc-doc" data-testid="slack-share-doc">
      <div className="ssc-doc-head">
        <span className="ssc-doc-kind">{target.kind_label}</span>
        <span className="ssc-doc-title">{target.title}</span>
      </div>
      {summary ? <div className="ssc-doc-summary">{summary}</div> : null}
      <div className="ssc-doc-link">{target.url}</div>
    </div>
  )
}

function ResolvedRecord({ resolution }: { resolution: SlackShareResolution }) {
  const line =
    resolution.outcome === "sent"
      ? `Shared to #${resolution.channelName}.`
      : resolution.outcome === "cancelled"
        ? "Not shared — nothing was posted to Slack."
        : `Couldn't share it: ${resolution.error}`
  return (
    <div
      className={`ssc-card ssc-card--resolved ssc-card--${resolution.outcome}`}
      data-testid="slack-share-resolved"
    >
      <span className="ssc-resolved-mark" aria-hidden>
        {resolution.outcome === "sent" ? "✓" : resolution.outcome === "cancelled" ? "—" : "!"}
      </span>
      <span className="ssc-resolved-text">{line}</span>
    </div>
  )
}

export function SlackSharePreviewCard({
  preview,
  busy = false,
  resolved,
  onSend,
  onCancel,
  onPickTarget,
  questionInPopup = false,
}: SlackSharePreviewCardProps) {
  const [note, setNote] = useState<string>(() => {
    // The note the planner drafted from the user's own intent ("ask the team
    // for feedback"), pre-filled and fully editable. Read out of the composed
    // blocks' first section rather than threaded separately — the server put
    // it there, and this is the one place it is needed.
    const first = preview.message?.blocks?.[0] as
      | { type?: string; text?: { text?: string } }
      | undefined
    if (first?.type === "section" && typeof first.text?.text === "string") {
      // Only when it ISN'T the document line: a note-less share puts the
      // document in block 0, and pre-filling the box with "*PRD:* <url|Title>"
      // would be nonsense the user has to delete.
      const t = first.text.text
      if (!t.startsWith("*")) return t
    }
    return ""
  })
  // Only a channel the SERVER resolved counts. There is no local pick to fold
  // in any more: the question is answered in the popup, which re-previews, so
  // by the time this card offers a Send the channel has been checked.
  const chosenChannel = preview.channel ?? null

  // Hooks are all above this line, so the hook order is identical on the
  // render where the card flips resolved (the rule ClarifyQuestionsCard
  // records).
  if (resolved) return <ResolvedRecord resolution={resolved} />

  // ── the kind cannot be shared ──
  if (preview.status === "unsupported_type") {
    return (
      <div className="ssc-card ssc-card--blocked" data-testid="slack-share-unsupported">
        <div className="ssc-line">
          {preview.named_type ? `A ${preview.named_type}` : "That"} can&apos;t be shared to
          Slack — PRDs, tickets, reports and documents can. Open it in Sprntly and
          share the link if you need to send it across.
        </div>
      </div>
    )
  }

  // ── which document? ──
  if (preview.status === "needs_target" || preview.status === "ambiguous_target") {
    const candidates = preview.candidates ?? []
    // The choice itself lives in the popup; this is the one-line pointer that
    // stands in for it, exactly as `cqc-popup-note` does for the clarify card.
    if (questionInPopup && candidates.length) {
      return (
        <div className="ssc-card" data-testid="slack-share-popup-note">
          <div className="ssc-intro">
            That matched {candidates.length} documents — pick the one you meant below.
          </div>
        </div>
      )
    }
    return (
      <div className="ssc-card" data-testid="slack-share-pick-target">
        <div className="ssc-intro">
          {candidates.length
            ? "Which one do you want to share?"
            : "I couldn't find that document. Open it in Sprntly and try again, or name it more precisely."}
        </div>
        {candidates.length ? (
          <div className="ssc-choices">
            {candidates.map((c) => (
              <button
                key={`${c.type}-${c.id}`}
                type="button"
                className="bc-action-btn ssc-choice"
                data-testid="slack-share-target-option"
                disabled={busy || !onPickTarget}
                onClick={() => onPickTarget?.(c)}
              >
                <span className="ssc-choice-kind">{c.kind_label}</span>
                <span className="ssc-choice-title">{c.title}</span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="ssc-actions">
          <button
            type="button"
            className="bc-action-btn"
            data-testid="slack-share-cancel"
            disabled={busy}
            onClick={onCancel}
          >
            Never mind
          </button>
        </div>
      </div>
    )
  }

  const target = preview.target
  if (!target) return null

  // ── a private channel Sprntly cannot join ──
  if (preview.status === "blocked") {
    return (
      <div className="ssc-card ssc-card--blocked" data-testid="slack-share-blocked">
        <div className="ssc-line">{preview.warning}</div>
        <div className="ssc-actions">
          <button
            type="button"
            className="bc-action-btn"
            data-testid="slack-share-cancel"
            disabled={busy}
            onClick={onCancel}
          >
            OK
          </button>
        </div>
      </div>
    )
  }

  const needsChannel = preview.status === "needs_channel"
  const noChannelsAtAll = needsChannel && (preview.channels ?? []).length === 0
  const canSend = !busy && !!chosenChannel

  return (
    <div className="ssc-card" data-testid="slack-share-preview">
      <div className="ssc-intro">
        {needsChannel
          ? noChannelsAtAll
            ? "Sprntly can't see any Slack channels — check the Slack connection in Sources."
            : preview.channel_query
              ? `I couldn't find #${preview.channel_query} — pick a channel below and I'll post this.`
              : "Pick a channel below and I'll post this:"
          : `Ready to post to #${chosenChannel?.name}. Here's what the team will see:`}
      </div>

      {/* The message, split: the user's words above, the document below. */}
      <div className="ssc-message" data-testid="slack-share-message">
        <textarea
          className="ssc-note"
          data-testid="slack-share-note"
          value={note}
          placeholder="Add a message for the team… (optional)"
          disabled={busy}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canSend) {
              e.preventDefault()
              onSend(chosenChannel!.id, note)
            }
          }}
        />
        <DocumentBlock target={target} summary={preview.message?.summary ?? ""} />
      </div>

      {/* A public channel Sprntly hasn't joined yet — stated before the send,
          not discovered after it. */}
      {preview.warning && preview.status === "ready" ? (
        <div className="ssc-warning" data-testid="slack-share-warning">
          {preview.warning}
        </div>
      ) : null}

      <div className="ssc-actions">
        {/* No Send at all while the channel is still open — a disabled button
            reading "Pick a channel" would compete with the popup that IS the
            picker. The note stays editable meanwhile, so a user can write
            what they want to say while deciding where it goes. */}
        {chosenChannel ? (
          <button
            type="button"
            className="bc-action-btn bc-action-btn--primary"
            data-testid="slack-share-send"
            disabled={!canSend}
            onClick={() => onSend(chosenChannel.id, note)}
          >
            {busy ? "Posting…" : `Post to #${chosenChannel.name}`}
          </button>
        ) : null}
        <button
          type="button"
          className="bc-action-btn"
          data-testid="slack-share-cancel"
          disabled={busy}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

/** The card wrapped in the standard agent chrome, for hosts that render it as
 *  a standalone message rather than inside an existing agent body. */
export function SlackShareMessage(props: SlackSharePreviewCardProps) {
  return (
    <div className="bc-turn">
      <div className="bc-agent-head">
        <span className="bc-agent-mark">
          <IconSparkle size={14} />
        </span>
        <span className="bc-agent-name">Sprntly</span>
        <span className="bc-agent-badge">
          <IconSparkle size={10} />
          SHARE
        </span>
      </div>
      <div className="bc-agent-body">
        <SlackSharePreviewCard {...props} />
      </div>
    </div>
  )
}
