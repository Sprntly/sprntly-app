"use client"

// The interactive share-to-Slack CARD handlers, shared by every chat surface.
//
// The PREVIEW that seeds a card is raised by the shared `runShareToSlackAction`
// (wired through `onShareToSlack` in each surface's submit path). These are the
// POST-preview steps the card + its dock question drive: sending, re-previewing
// on a picked document, and settling the channel/target question. They were
// inlined identically on the main screen (`ChatScreen`) and copied onto the
// project conversation host — this is the one shared unit both now call.
//
// The unit is keyed on TURN ID only. Each surface injects four seams that
// address its own store: `patchTurn`/`getShare` reach the turn's `slackShare`
// (main binds them to the ACTIVE tab — the only tab a card is ever visible on;
// project binds them to its single conversation), and `getPendingShare`/
// `setPendingShare` reach the surface's one open dock question (main = a
// tab-scoped `setTabs` patch, project = its single-conv `pendingShare` state).
// The `slackShareApi.send`/`.preview` ordering and the re-preview-on-answer
// logic live here verbatim, so the two surfaces can no longer drift.

import { useCallback } from "react"

import { slackShareApi, type SlackShareTarget, type SlackShareTargetRef } from "../../../../lib/api"
import { slackShareQuestionFor, type SlackShareQuestion } from "../../../../lib/chat/slackShareQuestion"
import type { PopupAnswer } from "../../QuestionPopup"
import type { ThreadTurn } from "../../../screens/app/ChatScreen"

/** The card state riding a turn — the shared shape of `ThreadTurn["slackShare"]`. */
type SlackShareState = NonNullable<ThreadTurn["slackShare"]>

/** A dock question awaiting an answer, tagged with the card's turn. Both
 *  surfaces' `pendingShare` state is structurally this. */
export type PendingShareState = { turnId: string } & SlackShareQuestion

/** The per-surface store seams the handlers address, all keyed on turn id.
 *  Main binds these to its active tab; the project host to its one conversation. */
export type SlackShareCardSeams = {
  /** Patch one turn's `slackShare` in place (the surface's `patchSlackShare`). */
  patchTurn: (turnId: string, patch: Partial<SlackShareState>) => void
  /** Read a turn's current `slackShare` (null if the turn has none / is gone). */
  getShare: (turnId: string) => SlackShareState | undefined
  /** Read the surface's one open share dock question, if any. */
  getPendingShare: () => PendingShareState | undefined
  /** Set (or clear) the surface's open share dock question. */
  setPendingShare: (ps: PendingShareState | undefined) => void
}

export function useSlackShareCardHandlers(seams: SlackShareCardSeams) {
  const { patchTurn, getShare, getPendingShare, setPendingShare } = seams

  /** The one place anything is actually posted to Slack (card's Send button). */
  const sendSlackShare = useCallback(async (
    turnId: string, channelId: string, note: string,
  ) => {
    const share = getShare(turnId)
    if (!share || share.resolved || share.busy) return
    const channelName =
      share.preview.channel?.name
      ?? (share.preview.channels ?? []).find((c) => c.id === channelId)?.name
      ?? "the channel"
    patchTurn(turnId, { busy: true })
    try {
      await slackShareApi.send(share.ref, channelId, note)
      patchTurn(turnId, { busy: false, resolved: { outcome: "sent", channelName } })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Slack rejected the message"
      patchTurn(turnId, { busy: false, resolved: { outcome: "failed", error: msg } })
    }
  }, [getShare, patchTurn])

  /** The user picked which document from an ambiguous match — re-preview on
   *  that one, keeping the channel and note they already had. */
  const repreviewSlackShare = useCallback(async (
    turnId: string, target: SlackShareTarget,
  ) => {
    const share = getShare(turnId)
    if (!share || share.resolved) return
    const ref: SlackShareTargetRef =
      target.type === "prd" ? { prd_id: target.id }
      : target.type === "report" ? { report_id: target.id }
      : target.type === "ticket_set" ? { ticket_set_id: target.id }
      : { custom_artifact_id: target.id }
    patchTurn(turnId, { busy: true })
    try {
      const preview = await slackShareApi.preview(ref, {
        channel: share.preview.channel?.name ?? share.preview.channel_query ?? null,
      })
      patchTurn(turnId, { busy: false, ref, preview })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      patchTurn(turnId, { busy: false, resolved: { outcome: "failed", error: msg } })
    }
  }, [getShare, patchTurn])

  /** The share question settled — re-preview on the answer.
   *
   *  A re-preview rather than a local patch, and for both kinds: the server is
   *  what knows whether the chosen channel is one Sprntly can post to, and
   *  patching `status: "ready"` client-side would offer a Send for a private
   *  channel the bot cannot join. One round trip buys the same guarantees the
   *  first preview gave.
   *
   *  `_tabId` is the surface-agnostic `activeTabId` the shared ConversationView
   *  passes; the seams already carry the surface's addressing, so it is unused. */
  const completeShareQuestion = useCallback(async (
    _tabId: string, answers: PopupAnswer[],
  ) => {
    const ps = getPendingShare()
    if (!ps) return
    setPendingShare(undefined)
    const share = getShare(ps.turnId)
    if (!share || share.resolved) return

    const picked = answers.find((a) => !a.skipped)
    if (!picked) {
      // Skipped or dismissed — nothing was posted, and the card says exactly
      // that rather than leaving an unanswered question in the thread.
      patchTurn(ps.turnId, { resolved: { outcome: "cancelled" } })
      return
    }
    // A typed answer has no `value`; take the text (minus any leading '#') as
    // the channel name, which the server matches exactly like a picked one.
    const answer = (picked.value ?? picked.answer ?? "").trim()
    if (!answer) {
      patchTurn(ps.turnId, { resolved: { outcome: "cancelled" } })
      return
    }

    if (ps.kind === "target") {
      const target = (share.preview.candidates ?? [])
        .find((c) => `${c.type}-${c.id}` === answer)
      if (!target) return
      await repreviewSlackShare(ps.turnId, target)
      return
    }

    patchTurn(ps.turnId, { busy: true })
    try {
      const preview = await slackShareApi.preview(share.ref, {
        channel: answer.replace(/^#/, ""),
      })
      patchTurn(ps.turnId, { busy: false, preview })
      // A typed channel that still doesn't resolve asks again rather than
      // silently dropping the share.
      const next = slackShareQuestionFor(preview)
      if (next) setPendingShare({ turnId: ps.turnId, ...next })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      patchTurn(ps.turnId, { busy: false, resolved: { outcome: "failed", error: msg } })
    }
  }, [getPendingShare, setPendingShare, getShare, patchTurn, repreviewSlackShare])

  /** Dismissing the question settles the share as NOT SENT. Deliberate: an
   *  abandoned question would otherwise leave a thread whose last word is
   *  "here's what I'll post" about a message that never went anywhere. */
  const cancelShareQuestion = useCallback((_tabId: string) => {
    const ps = getPendingShare()
    setPendingShare(undefined)
    if (ps) patchTurn(ps.turnId, { resolved: { outcome: "cancelled" } })
  }, [getPendingShare, setPendingShare, patchTurn])

  return { sendSlackShare, repreviewSlackShare, completeShareQuestion, cancelShareQuestion }
}
