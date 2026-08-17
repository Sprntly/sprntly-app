"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { useAuth } from "../../../lib/auth"
import { chatIntentEnvelopeOn } from "../../../lib/onboarding/types"
import { dispatchChatIntent } from "../../../lib/chat/dispatchChatIntent"
import { slackShareQuestionFor } from "../../../lib/chat/slackShareQuestion"
import {
  providerNoticeFromEnvelope,
  providerNoticeTitle,
  type ProviderNotice,
} from "../../../lib/providerLimitNotice"
import type { ChatHomeCard, ConversationRow } from "../../../types/content"
import { buildHomeChips, type HomeChipItem } from "../../../lib/homeChips"
import { AppLayout } from "./AppLayout"
import { BriefChat, isPrdCommand, isPrdEditCommand, isTicketsCommand, mentionsPrd, prdCommandTask } from "../../shared/BriefChat"
import { EmptyPane } from "../../shared/EmptyPane"
import { AssistantThinkingSkeleton } from "../../shared/AssistantThinkingSkeleton"
import {
  AssistantWaitState,
  WAIT_FAILED_TITLE,
  isLongRunningSkill,
} from "../../shared/AssistantWaitState"
import { PrdInputQuestions, clearPrdDrafts, prdStateFromRecord } from "../../shared/PrdInputQuestions"
import {
  clarifyAnswersText,
  clarifyQuestionsText,
  type ClarifyAnswer,
  type ClarifyQuestion,
  type ClarifyResolution,
} from "../../shared/ClarifyQuestionsCard"
import { QuestionPopup, type PopupAnswer } from "../../shared/QuestionPopup"
import {
  SlackShareMessage,
  type SlackShareResolution,
} from "../../shared/SlackSharePreviewCard"
import { ChatSuggestionIcon, IconDocument, IconSparkle } from "../../shared/app-icons"
import { IconFolder } from "@tabler/icons-react"
// The strip's reopen button is icon-only, so the Evidence case needs an icon of
// its own — the same one ContentPanel's Evidence tab wears, so the button reads
// as "reopen that tab".
import { NextPromptSuggestions } from "../../shared/NextPromptSuggestions"
// The composer — extracted 2026-08-10 so the individual chat and the project
// group chat share ONE implementation instead of two.
import { ChatComposer, DRAFT_MAX_CHARS, DRAFT_MIN_CHARS, type PinnedSkill } from "../../shared/ChatComposer"
import { SlashSkillMenu } from "../../shared/SlashSkillMenu"
import { spliceSkill, resolveAttachmentRefs } from "../../shared/chatComposerController"
import {
  customArtifactsApi,
  type ChatIntentEnvelope,
  ApiError, artifactsApi, askApi, attachmentsApi, chatSuggestionsApi, slackShareApi, storiesApi, ticketDataApi, type AskResponse, type ChatArtifactItem, type OpenArtifactCandidate, type OpenArtifactResult, type ReportSummary, type SkillInfo, type SlackSharePreview, type SlackShareTarget, type SlackShareTargetRef, type TicketAssignQuestion,
} from "../../../lib/api"
import { createChatPersistence, replyToText } from "../../../lib/chatPersistence"
import { addToSet, isComposerBusy, removeFromSet, runTabAsk } from "../../../lib/chatAskState"
import { useSpeechInput } from "../../../lib/useSpeechInput"
import { runPrdGeneration, resumePrdGeneration, runPrdGenerationFromIdeation, loadPrdById } from "../../../lib/runPrdGeneration"
// resumePrdGeneration re-enters polling for an already-kicked-off PRD (the import path).
import type { PrdTabRequest } from "../../../context/NavigationContext"
import { runEvidenceGeneration, resumeEvidenceGeneration, loadEvidenceByInsight } from "../../../lib/runEvidenceGeneration"
import { runAskGeneration, resumeAskGeneration, getPendingAsk, AskCancelledError, AskStoppedError, AskTimeoutError } from "../../../lib/runAskGeneration"
// The ONE owner of a standalone ticket-set run and of `content.ticketSet`.
// Nothing in this file may call `storiesApi.generateFromInsight` directly —
// see the module header for why a second caller is a second LLM bill.
import { loadTicketSet, runTicketSetGeneration } from "../../../lib/runTicketSetGeneration"
import { getPendingJob, insightScope } from "../../../lib/jobResume"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import type { DetailState, PrdState, PrdContent, TicketSetFailureKind } from "../../../types/content"
import { useBriefPrototypeMap } from "../../design-agent/useBriefPrototypeMap"
import { GeneratePrototypeCTA } from "../../design-agent/GeneratePrototypeCTA"
import { prototypePath } from "../../../lib/routes"
import { documentPath } from "../../../(app)/artifacts/doc/DocumentRoute"
import { ChatBubble } from "../../shared/ChatBubble"
import { ChatTranscript, type ChatTranscriptTurn } from "../../shared/ChatTranscript"
import { mapMainTurns } from "./mapMainTurns"
import { ChatShell } from "../../shared/chat-shell/ChatShell"
import { useRouter, useSearchParams } from "next/navigation"
import { prototypeStateForInsight } from "../../design-agent/briefPrototypeMap.helpers"
import { AGENT_NAME } from "../../../lib/agent"

/** "This thread has no reports we can vouch for" — one shared instance so the
 *  scoped-reports memo below returns a STABLE reference, and the callbacks and
 *  memos that depend on it don't re-run on every render of a report-less chat. */
const NO_REPORTS: ReportSummary[] = []

export type ThreadTurn = {
  id: string
  /** DISPLAY text — the user's typed ask only. Attached-document content is NOT
   *  folded in here (that goes to the backend separately); the thread renders
   *  this plus a chip per `attachments` entry, the way Claude's chat does. */
  query: string
  /** Files attached to this turn, shown as clickable cards above the ask. Each
   *  carries the extracted/plain-text `content` so the card can open a viewer —
   *  this is the SAME text folded into the backend query, never re-fetched — plus
   *  a storage `key`/`mime` pointing at the ORIGINAL file so the viewer can render
   *  the real document (PDF/image inline) and offer a download after a reload. */
  attachments?: { name: string; content?: string; key?: string | null; mime?: string | null; size?: number | null }[]
  reply?: AskResponse
  error?: string
  /** The user stopped this ask before it answered (composer Stop button). Renders
   *  a muted "stopped" note instead of the thinking skeleton or an error bubble. */
  stopped?: boolean
  /** A reload killed this turn's in-flight work before its reply landed — set
   *  only by the sessionStorage RESTORE path (the persist effect marks a PRD
   *  command still awaiting its deferred reply; see deferredAckRef). Renders an
   *  honest "send it again" note instead of "No response was generated". */
  interrupted?: boolean
  /** The artifact chat summary for this turn is still being written (the
   *  panel's artifact is done; the summary call is in flight). Renders a
   *  "Summarizing…" indicator instead of appearing out of nowhere seconds
   *  later. Transient: resolved turns drop the flag, failed/empty summaries
   *  remove the whole turn, and the persist effect never saves a still-pending
   *  one (a reload cannot restore a skeleton nothing will ever fill). */
  summaryPending?: boolean
  /** The clarify gate's questions, STRUCTURED — rendered as an answerable card
   *  (options as buttons, one submit for the batch) instead of the flattened
   *  numbered list that `reply.answer` carries. Both live on the same turn: the
   *  card is what the user sees while the gate is open, and `reply.answer` is
   *  the durable form that persists, rehydrates and feeds the transcript filter.
   *  The card renders while the tab's `pendingClarify` is live and, once
   *  settled, as the read-only record below; only a full history rehydration
   *  (which rebuilds turns from text alone) falls back to `reply.answer`. */
  clarify?: ClarifyQuestion[]
  /** How the batch above was settled — answers given, or the assumptions each
   *  unanswered question fell back to. Its presence is what flips the card from
   *  input to record, so the structure the user answered in SURVIVES answering
   *  instead of collapsing back to an undifferentiated wall of text. */
  clarifyResolved?: ClarifyResolution
  /** Live answer markdown streamed over SSE while this turn's ask generates —
   *  rendered in place of the thinking skeleton so the reply appears
   *  word-by-word. Display only: `reply` (from the poll) is authoritative and
   *  replaces it. Transient — stripped from the persisted thread, because a
   *  reload can't re-attach the stream that was feeding it. */
  partial?: string
  /** The SSE preview channel dropped after it had already delivered text, while
   *  the poll still reports `generating`. Display only — it downgrades the phase
   *  line to "Finishing the answer" and names what happened, because the old
   *  behaviour was a half-sentence frozen under a blinking cursor with no
   *  explanation. Never an error: the poll still delivers the real answer.
   *  Transient, like `partial`. */
  streamDropped?: boolean
  /** "Open the PRD for X" matched SEVERAL documents — the candidates, rendered
   *  as chips under this turn's reply. Each chip carries its artifact's ids and
   *  opens the panel on click; it does NOT re-send its label as a message,
   *  which is what made the old suggestion chip inert (it asked the same
   *  question again and got the document read back as chat text).
   *
   *  PERSISTED with the turn (it rides the normal slim payload — only the
   *  streaming fields below are stripped), so the chips are still answerable
   *  after a reload instead of leaving an unanswerable question behind. */
  openCandidates?: OpenArtifactCandidate[]
  /** "What are my PRDs?" — the user's own artifacts, rendered as clickable
   *  cards under this turn's reply. Same contract as `openCandidates`: each
   *  card carries the artifact's ids and OPENS it on click (in its own thread
   *  when one survives), never re-sends its label as a message. Persisted
   *  with the turn, so the listing is still clickable after a reload. */
  artifactList?: ChatArtifactItem[]
  /** share_to_slack — the preview card riding this turn: what will be posted,
   *  where, and (once settled) what happened.
   *
   *  PERSISTED with the turn, like `openCandidates`, and `resolved` is the
   *  reason it must be. A share that reloaded back into its unsettled state
   *  would offer a Send button for a message that may already have gone out;
   *  a settled one reloads as the record of what was posted, which is the only
   *  honest thing a thread can say about a message in a team channel. */
  slackShare?: {
    /** What to post — the same reference the preview resolved, sent back
     *  verbatim so `send` re-reads the identical document. */
    ref: SlackShareTargetRef
    preview: SlackSharePreview
    resolved?: SlackShareResolution
    busy?: boolean
  }
  /** The 12-minute client budget expired while the job was still generating.
   *  NOT a failure — the persisted ask_id is deliberately left in place, so a
   *  reload re-attaches and picks the answer up. Transient: after a reload the
   *  resume effect puts this turn back into the generating state, which is what
   *  the message promises. */
  timedOut?: boolean
}

/** Artifact kinds a user can NAME in an open request that this panel does not
 *  render. Each one opens somewhere — just not here — so the reply names the
 *  thing they asked for rather than quietly handing back a PRD of that name. */
const UNSUPPORTED_OPEN_KIND: Record<string, string> = {
  prototype: "A prototype",
  report: "A report",
  tickets: "Tickets",
}

type BriefMeta = { briefId: number; insightIndex: number }

type ChatTab = {
  id: string
  title: string
  thread: ThreadTurn[]
  dbConvId: number | null
  /** Brief finding context — enables PRD/evidence generation for this tab. */
  briefMeta: BriefMeta | null
  /** The originating insight's body/description text, shown under the title in
   *  the opening insight message. Null for tabs not opened from a brief finding
   *  (backlog / plain chat) or when the finding had no body. */
  insightBody: string | null
  /** Per-tab cached PRD (not persisted to localStorage — reload restores it from
   *  the DB by `prdId`). */
  prd: PrdState | null
  /** This tab's OWN saved PRD id. Unlike the full `prd`, this small number IS
   *  persisted, so a reload can DB-load the exact PRD this tab is about — no
   *  regeneration, no reliance on the (mutable) brief insight→PRD map. It's the
   *  only recovery path for backlog PRDs, whose tabs carry no `briefMeta`. */
  prdId: number | null
  /** Per-tab cached evidence. */
  evidence: PrdContent | null
  /** The cached evidence's row id — what an ask from this tab sends as
   *  `evidence_id` so "this evidence" is answered from the open document.
   *  Optional and best-effort: stamped wherever evidence lands on the tab
   *  (generation, resume, the existence probe); a tab that never learned
   *  the id simply asks without it — exactly the pre-feature behaviour.
   *  A tab with a PRD never sends it: the PRD context already carries its
   *  evidence. */
  evidenceId?: number | null
  /** This tab was opened from a Top Insights card's "View Evidence" — the
   *  finding's EVIDENCE is what it is about, so refocusing it restores the panel
   *  on the Evidence tab rather than closing it (a tab with no PRD) or jumping to
   *  a PRD that merely happens to exist for the same insight. Cleared in effect
   *  once a PRD actually lands on the tab, which then takes precedence. PERSISTED
   *  (small boolean) so the behaviour survives a reload. */
  evidenceOnly?: boolean
  /** The originating finding's drill-down state, which scopes ContentPanel's
   *  Evidence tab to this insight (it loads/generates from `detail.meta`).
   *  Transient — never persisted; after a reload an `evidenceOnly` tab falls back
   *  to the read-only load keyed off `briefMeta`. */
  evidenceDetail?: DetailState | null
  prdGenerating: boolean
  /** True while an EXISTING PRD is being fetched from the DB — distinct from
   *  `prdGenerating`, which means a document is being written.
   *
   *  The load path used to borrow `prdGenerating`, so reopening a chat that
   *  already HAS a PRD flashed "Generating PRD…" before settling on "View PRD" —
   *  claiming work was underway when the PRD had been finished for hours.
   *  Transient; never persisted. */
  prdLoading?: boolean
  evidenceGenerating: boolean
  /** This PRD tab was opened by an IN-CHAT COMMAND (import a doc + "generate prd",
   *  "generate a PRD for X", "create tickets from this doc") — its first thread
   *  turn IS the user's command. When true, the insight/PRD card + clarifying
   *  questions render INLINE as the reply BELOW that command turn (chronological
   *  order), not pinned above the whole thread. Falsy for header opens (brief
   *  insight / ideation / backlog-load), where the insight card IS the tab's
   *  opening message and stays at the top. PERSISTED (in the slim tab payload) so
   *  the ordering survives reload — must NOT be stripped like `prd`/`prdGenerating`. */
  prdInFlow?: boolean
  /** The thread id of the command turn a `prdInFlow` tab anchors its inline PRD
   *  card + questions to. A command typed in a REUSED chat tab lands mid-thread,
   *  so `thread[0]` (the legacy anchor) is the wrong turn there. PERSISTED with
   *  the thread (same slim payload), whose turn ids it references. */
  prdFlowTurnId?: string
  /** True while a resumed conversation's turns are being fetched in the
   *  background (row click in All chats navigates instantly; the tab shows a
   *  loading state until the history lands). Transient — never persisted. */
  hydrating?: boolean
  /** Clarify-first gate (issue d): set when the sufficiency check found the
   *  task too thin and posted questions into this tab's thread. The NEXT
   *  message in this tab is treated as the answers (or a "generate now" skip)
   *  and generation runs with the combined task. Transient — never persisted;
   *  a reload simply drops back to a fresh command. */
  pendingClarify?: {
    task: string
    sourceDocs?: { name: string; content: string }[]
    /** The thread turn carrying the questions, so whichever path answers them —
     *  the card's submit or a prose reply in the composer — can stamp its
     *  resolution back onto the right turn. */
    turnId: string
  }
  /** Assign-tickets (chat): the plan's OPEN questions while the dock's popup
   *  steps through them. Picks stay local until the LAST question settles —
   *  then completeAssign writes every pair (the same PUT the drawer's picker
   *  makes) and posts the summary. Transient — never persisted; a reload
   *  drops the open questions and the user re-asks. */
  pendingAssign?: {
    questions: TicketAssignQuestion[]
    /** Human lines for the pairs the PLAN already applied (the explicit ones,
     *  written before the popup opened) — they lead the final summary. */
    applied: string[]
    /** The flow's turn, so the summary lands on the same conversation entry. */
    turnId: string
  }
  /** A share_to_slack question the popup is holding: which CHANNEL to post to,
   *  or which DOCUMENT was meant.
   *
   *  Every choice this product asks for goes through the QuestionPopup (owner's
   *  directive, 2026-08-16, after the first cut rendered a row of channel chips
   *  inside the preview card) — the clarify gate, ticket assignment and this
   *  now all ask the same way, in the dock above the composer.
   *
   *  Transient like `pendingAssign`: a reload drops the open question, and the
   *  share it belongs to is left unsettled rather than posted. */
  pendingShare?: {
    /** Which turn's card this question belongs to. */
    turnId: string
    /** "channel" — the answer is a channel NAME (re-previewed server-side so
     *  membership and the private-channel block are re-checked on the real
     *  pick). "target" — the answer is a `${type}-${id}` key into the
     *  preview's own candidates. */
    kind: "channel" | "target"
    header: string
    prompt: string
    options: { label: string; description?: string | null; value: string }[]
  }
  /** True from the moment a PRD command is acknowledged until the agent's NEXT
   *  visible response — the clarifying questions, or the generation starting.
   *  Drives a live thinking indicator under the acknowledgment.
   *
   *  Without it that window is dead air: the ack says a PRD is coming, the rail
   *  is deliberately not open yet (it may turn out to be questions, not a
   *  document), and nothing on screen moves while the sufficiency check runs —
   *  which reads as a hung app. Transient; never persisted. */
  prdCommandThinking?: boolean

  // ── Standalone ticket set (a chat with NO PRD) ────────────────────────────
  /** THE double-generation guard. True from the moment a ticket run is kicked
   *  off on this tab until it reaches a terminal state.
   *
   *  Not belt-and-braces: the PRD path can afford a re-kick because the backend
   *  dedupes on `(company_id, prd_id, insight)` and the result is a
   *  content-hashed cache. The insight path has no such key — the chat's
   *  insight is an LLM-composed string, so "make tickets for this" and "turn
   *  that into tickets" are two different insights, two `ticket_sets` rows and
   *  two multi-minute LLM bills for work the user asked for once. Transient;
   *  never persisted (a reload must not restore a latch for a run it can no
   *  longer observe). */
  ticketSetRunning?: boolean
  /** `ticket_sets.id` of the set this chat produced — the newest one, if the
   *  thread has several (a second ask creates a second set by design). Drives
   *  the reply-footer's one-button action row. Transient; the thread-resume
   *  probe re-establishes it from GET /v1/ticket-sets/by-conversation. */
  ticketSetId?: number | null
  /** Last known lifecycle of `ticketSetId`: "generating" | "ready" | "failed".
   *  Transient, for the same reason as the latch. */
  ticketSetStatus?: string | null
  /** The request the run was started from, kept so the footer's "Retry tickets"
   *  can re-run it without a round-trip. Transient — after a reload the retry
   *  reads `source_text` back off the row instead. */
  ticketSetTask?: string
}

// `clarifyQuestionsText`/`clarifyAnswersText` — the clarify gate's durable
// text formatters — now live in `ClarifyQuestionsCard.tsx` (the shared card's
// natural home, reused by the private project engine too) and are re-exported
// here so this file's own callers below (and `ChatScreen.clarify-card.dom.
// test.tsx`, which imports them from this module) keep resolving unchanged.
// `PRD_CLARIFY_ANSWER_RE` matches `clarifyQuestionsText`'s output to keep the
// questions out of the PRD's grounding transcript, so its leading sentence
// must keep its "Before I write this PRD" opening.
export { clarifyAnswersText, clarifyQuestionsText }

function clarifyQuestionsReply(questions: ClarifyQuestion[]): AskResponse {
  return {
    answer: clarifyQuestionsText(questions),
    sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
  } as AskResponse
}

// "generate now" / "just proceed" — the user declines the clarify questions
// and wants the PRD from the original material as-is.
const CLARIFY_SKIP_RE =
  /^\s*(?:just\s+)?(?:generate|proceed|go\s+ahead|skip|continue)(?:\s+(?:it|now|anyway|as\s+is|without|the\s+prd))?\s*[.!]*\s*$/i

// The Top Insights brief is a pinned, non-closable FIRST tab on this surface.
// It is synthesized in the render — never stored in the `tabs` state or
// localStorage — and is identified by this sentinel id. `activeTabId ===
// BRIEF_TAB_ID` means the brief tab is active (so we render <BriefChat/> instead
// of the chat landing/thread). It is also the default active tab on first load.
const BRIEF_TAB_ID = "brief"

// Placeholder title for a freshly-opened "+" tab before the user sends their
// first message. The tab is visible+active in the strip immediately (so the user
// can see they're on a new tab and switch back), and gets its real title from the
// first message on send (see submitAsk's first-send rename).
export const NEW_CHAT_TITLE = "New chat"

// Build a compact task string from a tab's conversation to seed a chat PRD when
// a "generate a PRD" command (or the Generate PRD button) carries no explicit
// topic. Uses the user's own turns — the problem/intent they described — joined
// and capped to the backend's 4000-char task limit. Returns "" when the tab has
// no conversation to seed from (the caller then asks for a topic instead of
// opening an unrelated brief PRD).
const CHAT_PRD_TASK_MAX = 3500
function conversationToPrdTask(thread: ThreadTurn[]): string {
  const joined = thread
    .map((t) => t.query?.trim())
    // Skip the PRD/import commands that merely opened artifacts — they're not the
    // problem the user is discussing, so they'd make a nonsensical PRD seed.
    .filter((q): q is string => !!q && !isPrdCommand(q))
    .join("\n\n")
    .trim()
  return joined.length > CHAT_PRD_TASK_MAX ? `${joined.slice(0, CHAT_PRD_TASK_MAX)}…` : joined
}

// Documents attached EARLIER in this tab's conversation — extracted text lives
// on each turn (stamped after extraction on live sends, rehydrated from
// conversation_turns.attachments on reloaded threads). A "generate a PRD"
// command grounds on these: the reported bug was a document attached two
// messages before the command being silently forgotten. Caps mirror the
// backend's TaskSourceDoc limits; when over the doc cap, the MOST RECENT
// attachments win (they're most likely what the user means).
const PRD_DOC_MAX_CHARS = 60000
const PRD_DOCS_MAX = 8
export function conversationPrdDocs(
  thread: ThreadTurn[],
): { name: string; content: string }[] {
  const docs: { name: string; content: string }[] = []
  for (const t of thread) {
    for (const a of t.attachments ?? []) {
      if (a.content?.trim()) docs.push({ name: a.name, content: a.content.slice(0, PRD_DOC_MAX_CHARS) })
    }
  }
  return docs.slice(-PRD_DOCS_MAX)
}

// The conversation ITSELF as grounding material.
//
// "Generate a PRD for this" means "build it from what we've been discussing" —
// and the substance usually sits in the AGENT's replies (a fetched Jira ticket,
// a retrieved finding, a summary), not only in what the user typed.
// conversationToPrdTask reads user turns only, so a request like
//   "get me a ticket of car"  →  <the KAN-1033 ticket>  →  "generate a prd for this"
// sent the backend five words and left the ticket in the browser. With nothing
// else to go on, KG retrieval grounded the document on whatever the workspace
// happened to be about, and the PRD came back about an unrelated subject.
//
// This rides the EXISTING source-doc channel, which the backend layers on top of
// KG grounding as authoritative material (routes/prd.py `extra_source_md`), so
// the thread leads and the workspace stays the supporting layer. It deliberately
// does NOT replace grounding — that is the uploaded-file import path
// (`import_source_md`), which is untouched.
const PRD_TRANSCRIPT_DOC_NAME = "Conversation (this chat)"
// Sprntly's own process chatter about making a PRD is not product material: the
// command acknowledgment and the clarify gate's question list would both read as
// requirements if fed back in. Matched on TEXT, not turn ids, so the filter still
// holds for a thread rehydrated from Supabase (which rebuilds turns with fresh
// ids and reconstructs replies as plain answers).
const PRD_ACK_ANSWER_RE = /View PRD button/
const PRD_CLARIFY_ANSWER_RE = /^Before I write this PRD/
// Artifact summaries end with a per-kind pointer line ("…View Evidence button…",
// "…View Prototype button…") precisely so this text-based filter can strip them
// here: a summary OF an artifact fed back as grounding would read as fresh
// requirements. PRD summaries are already covered by PRD_ACK_ANSWER_RE.
const EVIDENCE_SUMMARY_ANSWER_RE = /View Evidence button/
const PROTOTYPE_SUMMARY_ANSWER_RE = /View Prototype button/
// A standalone ticket set writes TWO agent turns — the acknowledgment on send
// and the summary on completion — and both end with the same pointer line, so
// one regex keeps both out of a later PRD's grounding. That matters more here
// than elsewhere: the whole point of the set is that the ticket bodies stopped
// being printed into the bubble, and feeding the ack back would reintroduce
// "here is what I'm writing" as if it were a requirement.
const TICKET_SET_ANSWER_RE = /View Tickets button/
export function conversationTranscriptDoc(
  thread: ThreadTurn[],
): { name: string; content: string } | null {
  const parts: string[] = []
  for (const t of thread) {
    const q = t.query?.trim()
    if (q) parts.push(`User: ${q}`)
    const a = typeof t.reply?.answer === "string" ? t.reply.answer.trim() : ""
    if (
      a &&
      !PRD_ACK_ANSWER_RE.test(a) &&
      !PRD_CLARIFY_ANSWER_RE.test(a) &&
      !EVIDENCE_SUMMARY_ANSWER_RE.test(a) &&
      !PROTOTYPE_SUMMARY_ANSWER_RE.test(a) &&
      !TICKET_SET_ANSWER_RE.test(a)
    ) {
      parts.push(`Sprntly: ${a}`)
    }
  }
  const joined = parts.join("\n\n").trim()
  if (!joined) return null
  // Newest wins on overflow — the tail of a long chat is what "this" refers to.
  return {
    name: PRD_TRANSCRIPT_DOC_NAME,
    content: joined.length > PRD_DOC_MAX_CHARS ? joined.slice(-PRD_DOC_MAX_CHARS) : joined,
  }
}

// Everything a chat-command PRD grounds on: documents attached in this thread
// PLUS the conversation. The transcript takes one of the backend's 8 doc slots,
// so attachments yield a slot rather than silently pushing it out, and it goes
// LAST so the most recent context sits closest to the end of the prompt.
export function prdGroundingDocs(
  thread: ThreadTurn[],
): { name: string; content: string }[] {
  const attachments = conversationPrdDocs(thread)
  const transcript = conversationTranscriptDoc(thread)
  if (!transcript) return attachments
  return [...attachments.slice(-(PRD_DOCS_MAX - 1)), transcript]
}

// ── ChatScreen-local PRD tab sources ────────────────────────────────────────
// The in-chat command flows ("convert this doc to a PRD", "generate a PRD for
// X") must render the tab's seed turn + generating skeleton on the CURRENT
// commit and only THEN hit the network — otherwise the composer clears, the
// message "leaves", and nothing shows for the multi-second import/generate,
// which reads as a frozen app. So instead of the caller awaiting
// importDoc/generateFromTask and passing a ready `resume` prd_id, these two
// kinds hand the *unstarted* work to openPrdInTab, which renders first and kicks
// the backend call INSIDE its async block (network AFTER the optimistic render).
type LocalPrdSource =
  // "convert this document to a PRD" — POST /v1/prd/import happens in-panel.
  // `artifactTemplateId` (both kinds) = the uploaded FORMAT the user named
  // ("…using our Acme format"), resolved to an id by the backend planner and
  // carried on the intent envelope. Undefined — the normal case — means the
  // company's active format. It rides the SOURCE rather than being read at the
  // call site because the network call happens inside `openPrdInTab`'s async
  // block, one commit after the decision was made.
  | {
      kind: "importDoc"; file: File; company: string; openTickets?: boolean
      artifactTemplateId?: string | null
    }
  // "generate a PRD for <task>" — POST /v1/prd/generate happens in-panel.
  // `sourceDocs` = documents attached earlier in the thread (extracted text);
  // the backend grounds the PRD on them alongside the task/KG evidence.
  | {
      kind: "generateTask"; task: string
      sourceDocs?: { name: string; content: string }[]
      artifactTemplateId?: string | null
    }
type LocalPrdTabRequest = Omit<PrdTabRequest, "source"> & {
  source: PrdTabRequest["source"] | LocalPrdSource
  /** Generate IN THIS TAB: pin the target to an existing chat tab so the PRD
   *  lands in the ACTIVE tab's artifacts panel (its command turn appended to the
   *  live thread) instead of spawning a new tab. Set by the in-chat command
   *  flows when the active tab is a plain, PRD-less chat. */
  inTabId?: string
  /** This open resolves ENTIRELY from the target tab's cache: `openPrdInTab`
   *  returns before reaching its async block, so nothing will ever run later to
   *  settle a deferred acknowledgment — the ack must ride the seed turn instead.
   *
   *  It is a field rather than a local because TWO functions have to agree on
   *  it: `openPrdInTab` decides whether the seed turn carries its reply inline,
   *  and `seedCommandTurn` — which runs AFTER it — decides whether to register
   *  the turn in `deferredAckRef`. The second cannot safely re-derive the answer
   *  from tab state the first has already started mutating.
   *
   *  Disagreement is not cosmetic. `settleCommandAck` writes the visible thread
   *  unconditionally but only PERSISTS when it finds a registered entry, so a
   *  settle arriving before the registration looks perfect on screen while
   *  silently dropping the assistant turn from the conversation — and the NEXT
   *  one then pairs its reply with the previous turn's id, inverting the
   *  user→assistant order `hydratePrdThread`'s rebuild depends on.
   *
   *  Computed once by the caller that knows whether the document is cached
   *  (`openArtifactInPanel`), read by both. */
  ackInline?: boolean
}

// The agent's acknowledgment for a command-opened PRD tab (seedQuery set on the
// request). Shown as the reply to the user's seeded command turn, so the chat
// explains what the spinning panel on the right is doing and how to get back to
// it (the PRD card above the thread hosts the View PRD button).
function commandAckReply(req: LocalPrdTabRequest): AskResponse {
  const source = req.source
  const importing = (source.kind === "resume" && source.origin !== "task") || source.kind === "importDoc"
  const fromTask = (source.kind === "resume" && source.origin === "task") || source.kind === "generateTask"
  const withTickets = (source.kind === "resume" || source.kind === "importDoc") && !!source.openTickets
  // An Ideation idea is NOT this week's top insight — it's one of the items the
  // brief did not prioritize — so it needs its own wording.
  const fromIdeation = source.kind === "generateIdeation"
  // WHERE the View PRD button actually lands, which differs by open kind and
  // must match the render (see `inlinePrdCards`): an in-chat command puts the
  // PRD card right below this reply, a header open pins it at the top of the
  // thread. The copy said "above" for every case, so a command-opened chat
  // pointed the user at a button that was in fact sitting under this message.
  const inline = source.kind === "importDoc" || source.kind === "generateTask"
    || ((source.kind === "resume" || source.kind === "load") && !!req.seedQuery)
  const locator = inline
    ? "Use the View PRD button just below to reopen the panel anytime."
    : "Use the View PRD button above to reopen the panel anytime."
  // OPENING an existing document, not producing one — so the copy must not
  // promise generation. Every other branch here describes work about to start;
  // this one describes a document that already exists arriving on screen, which
  // is the whole distinction the open_artifact action protects.
  const opening = source.kind === "load" || source.kind === "evidence"
  const lead = opening
    ? source.kind === "evidence"
      ? "Opening that evidence in the panel on the right."
      : "Opening that PRD in the panel on the right."
    : withTickets
    ? "Importing your document as a PRD — it'll open in the panel on the right, and I'll break it into tickets as soon as it's ready."
    : fromIdeation
    ? "Framing this Ideation idea as a PRD — it'll open in the panel on the right when ready. From there you can break it into tickets and generate a prototype."
    : fromTask
      ? "Generating a PRD for that — it'll open in the panel on the right when ready."
      : importing
        ? "Importing your document as a PRD — it'll open in the panel on the right when ready."
        : "Generating a PRD from this week's top insight — it'll open in the panel on the right when ready."
  return { answer: `${lead} ${locator}`, key_points: [], citations: [], confidence: 1, unanswered: "" }
}

/** An open that resolved to a real document the panel then refused to show —
 *  a PRD mid-regeneration is the live case (`loadPrdById` returns "PRD isn't
 *  ready yet").
 *
 *  This settles the deferred ack as ORDINARY PROSE rather than routing through
 *  failDeferredAck's error state, because it is neither a failure nor a
 *  generation: the shared error card says "That answer didn't come through.
 *  Nothing was saved." — three claims that are all false about an open that
 *  simply found the document busy. */
function openFailureReply(detail: string): AskResponse {
  const reason = detail.trim().replace(/[.\s]+$/, "")
  return {
    answer: reason
      ? `I couldn't open that PRD — ${reason.charAt(0).toLowerCase()}${reason.slice(1)}. Try again in a moment.`
      : "I couldn't open that PRD just now. Try again in a moment.",
    key_points: [], citations: [], confidence: 1, unanswered: "",
  } as AskResponse
}

/** Full-screen overlay that renders an attachment. When the ORIGINAL file was
 *  stored (`key`), it fetches a fresh signed URL and renders the real document —
 *  PDF/image inline, everything else offered as a download — falling back to the
 *  extracted text. Opened by clicking a file card on a user turn. */
function AttachmentViewer({
  attachment,
  onClose,
}: {
  attachment: { name: string; content: string; key?: string | null; mime?: string | null }
  onClose: () => void
}) {
  const [urls, setUrls] = useState<{ view_url: string; download_url: string; mime: string } | null>(null)
  const [status, setStatus] = useState<"idle" | "loading" | "error">(attachment.key ? "loading" : "idle")

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [onClose])

  // Sign-on-open: the stored URL expires, so mint a fresh one each time the
  // viewer opens. Best-effort — a failure falls back to the extracted text.
  useEffect(() => {
    if (!attachment.key) return
    let cancelled = false
    setStatus("loading")
    attachmentsApi.sign(attachment.key, attachment.name)
      .then((u) => { if (!cancelled) { setUrls(u); setStatus("idle") } })
      .catch(() => { if (!cancelled) setStatus("error") })
    return () => { cancelled = true }
  }, [attachment.key, attachment.name])

  const mime = urls?.mime || attachment.mime || ""
  const isPdf = /pdf/i.test(mime) || /\.pdf$/i.test(attachment.name)
  const isImage = /^image\//i.test(mime) || /\.(png|jpe?g|gif|webp)$/i.test(attachment.name)
  const hasText = !!attachment.content.trim()

  return (
    <div className="bc-file-viewer-backdrop" role="dialog" aria-modal="true" aria-label={attachment.name} onClick={onClose}>
      <div className="bc-file-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="bc-file-viewer-head">
          <span className="bc-file-viewer-title" title={attachment.name}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            {attachment.name}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            {urls?.download_url ? (
              <a
                className="bc-file-viewer-download"
                href={urls.download_url}
                download={attachment.name}
                target="_blank"
                rel="noopener noreferrer"
                title={`Download ${attachment.name}`}
                aria-label={`Download ${attachment.name}`}
                style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", padding: 6, color: "inherit", opacity: 0.75 }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
              </a>
            ) : null}
            <button type="button" className="bc-file-viewer-close" aria-label="Close" onClick={onClose}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </span>
        </div>
        <div className="bc-file-viewer-body">
          {attachment.key && status === "loading" ? (
            <p className="bc-file-viewer-empty">Loading document…</p>
          ) : urls && isPdf ? (
            <iframe
              src={urls.view_url}
              title={attachment.name}
              data-testid="attachment-pdf-frame"
              style={{ width: "100%", height: "100%", minHeight: "70vh", border: "none" }}
            />
          ) : urls && isImage ? (
            <img
              src={urls.view_url}
              alt={attachment.name}
              data-testid="attachment-image"
              style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain", display: "block", margin: "0 auto" }}
            />
          ) : hasText ? (
            <>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{attachment.content}</ReactMarkdown>
              {urls && !isPdf && !isImage ? (
                <p className="bc-file-viewer-empty">This file type can’t be previewed inline — use the download button above to open the original.</p>
              ) : null}
            </>
          ) : urls ? (
            <p className="bc-file-viewer-empty">This file type can’t be previewed inline — use the download button above to open the original.</p>
          ) : (
            <p className="bc-file-viewer-empty">No preview available for this file.</p>
          )}
        </div>
      </div>
    </div>
  )
}

/** Pressing Enter while an ask is in flight used to be a silent no-op — the
 *  keystroke simply vanished, with the draft still sitting there. The guard is
 *  correct (one ask per tab); the silence was the bug. */
export const BUSY_ENTER_HINT_LEAD = "Sprntly is still answering. Your message is saved — send it when the answer lands, or "
export const BUSY_ENTER_HINT_TAIL = " to interrupt."
/** How long the busy-Enter hint stays before clearing itself. */
const BUSY_HINT_MS = 6000

const DEFAULT_HOME_CHIPS: HomeChipItem[] = [
  { kind: "home", card: { id: "def-brief", icon: "sparkle", title: "View Top Insights brief", desc: "", target: "brief" } },
  { kind: "starter", card: { id: "def-analyze", icon: "chart", title: "Analyze data", desc: "", target: "ondemand", prompt: "Analyze our key product metrics and identify the top opportunities." } },
  { kind: "starter", card: { id: "def-draft", icon: "document", title: "Draft quarterly report", desc: "", target: "ondemand", prompt: "Draft a quarterly product report with key metrics, wins, and next steps." } },
  { kind: "starter", card: { id: "def-proto", icon: "rocket", title: "Prototype", desc: "", target: "ondemand", prompt: "Help me prototype the top feature in our product roadmap." } },
]

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

/** The acknowledgment a ticket command writes on the SAME commit as the send.
 *  The pointer sentence is load-bearing twice over: it tells the reader how to
 *  get back to a panel they may close, and it is what TICKET_SET_ANSWER_RE
 *  matches to keep this turn out of a later PRD's grounding. Note what it does
 *  NOT contain — any ticket text. The whole point of the set is that the bodies
 *  live in the panel instead of being printed into the bubble. */
const TICKET_SET_ACK =
  "Writing tickets for that — they'll open in the panel on the right when ready. " +
  "Use the View Tickets button in this chat to reopen them anytime."

/** Toast copy per failure KIND. The kind is all the runner returns — no backend
 *  message ever reaches this layer — so the words live here, beside the panel's
 *  own SET_ERROR_COPY rather than sharing it: a toast has one line, the panel
 *  has a whole empty state. */
const TICKET_SET_FAILURE_TOAST: Record<TicketSetFailureKind, string> = {
  timeout: "That run is taking longer than expected. It may still finish — reopen this chat in a few minutes.",
  network: "The connection dropped while the tickets were being written. Try again.",
  notfound: "Those tickets are no longer available.",
  failed: "The tickets couldn't be written from this conversation. Try again with more specifics.",
}

export function ChatScreen() {
  const {
    currentScreen,
    goTo,
    setAIBarValue,
    expandAiPanel,
    pendingSearchHandoff,
    setPendingSearchHandoff,
    pendingOndemandDraft,
    setPendingOndemandDraft,
    pendingChatHandoff,
    setPendingChatHandoff,
    pendingPrdTab,
    setPendingPrdTab,
    openPrdTab,
    pendingReportFocus,
    setPendingReportFocus,
    pendingTicketSetFocus,
    setPendingTicketSetFocus,
    showToast,
    openContentPanel,
    closeContentPanel,
    contentPanelTab,
  } = useNavigation()
  const router = useRouter()
  const searchParams = useSearchParams()
  const auth = useAuth()
  const { profile, workspace } = useWorkspace()
  const { content, setContent } = useContent()
  // A PRD generated in the main chat auto-forks into a project (server-side,
  // `maybe_auto_create_project_for_prd`), which returns the project id on the
  // generate response. We DON'T navigate away (the entry-flow reshape): the
  // page stays on `/`, the just-generated PRD stays open in the content panel,
  // and we simply RECORD the forked project id on the shared content state so
  // the panel can surface a project-menu affordance in its header. Because we
  // stay put we WANT the normal `?prd=…` reflect (no `skipArtifactReflectOnNavRef`
  // — that guard only existed to suppress the reflect during the old away-nav).
  // Best-effort: no id (an unbound generate, or an older backend) → no-op, the
  // panel renders exactly as it always has.
  const bindActiveProject = useCallback(
    (projectId: number | null | undefined) => {
      if (projectId == null) return
      setContent({ activeProjectId: projectId })
    },
    [setContent],
  )
  // Action dispatch is UNCONDITIONAL: one backend call (POST /v1/chat/intent,
  // backed by the Ask Planner — history-aware, sees the open PRD, the connected
  // sources and the company's skills) decides what every message asks for.
  //
  // There is no flag and no fallback ladder. Both existed while this competed
  // with a client-side regex cascade; that cascade is gone, so the kill switch
  // would now only choose between "the planner decides" and "nothing decides".
  const { activeCompany } = useCompany()
  const [railExpanded, setRailExpanded] = useState(false)
  const [activeConv, setActiveConv] = useState<number | null>(null)
  // Per-tab chat state is SESSION-scoped: it lives in sessionStorage, not
  // localStorage. So a fresh open (new browser tab/window, or reopening the app
  // after closing it) starts with ONLY the pinned Top Insights tab — never last
  // session's accumulated chat tabs. It still survives an in-session reload or a
  // navigate-away-and-back, so clicking around the app never nukes open chats.
  // Keys are ALSO user+company scoped so neither a different tenant nor a
  // different teammate signing in on this browser can see these tabs; sign-out
  // clears them outright (both storages) as defense in depth.
  const authUserId = auth.kind === "authed" ? auth.user.id : "anon"
  const tabsKey = `sprntly_chat_tabs_${authUserId}_${activeCompany}`
  const activeTabKey = `sprntly_chat_active_tab_${authUserId}_${activeCompany}`
  // The last CHAT tab the user was on — the pinned brief tab is never written
  // here. This is what the sidebar's "Workbench" nav (`/?tab=last`) restores, so
  // it always lands on real work rather than the brief. Session-scoped and
  // user+company-scoped for the same reasons as the tabs themselves.
  const lastTabKey = `sprntly_chat_last_tab_${authUserId}_${activeCompany}`

  const [tabs, setTabs] = useState<ChatTab[]>(() => {
    try {
      const saved = sessionStorage.getItem(tabsKey)
      if (!saved) return []
      // Restore with defaults for fields not persisted (prd/evidence are large — re-generate on reload)
      return (JSON.parse(saved) as Partial<ChatTab>[]).map((t) => ({
        id: t.id ?? "",
        title: t.title ?? "",
        thread: t.thread ?? [],
        dbConvId: t.dbConvId ?? null,
        briefMeta: t.briefMeta ?? null,
        insightBody: t.insightBody ?? null,
        prd: null,
        prdId: t.prdId ?? null,
        evidence: null,
        // Persisted (small int, like prdId): what an ask from this tab sends
        // as evidence_id, so "this evidence" still grounds after a reload.
        evidenceId: t.evidenceId ?? null,
        // Persisted: an evidence tab reopens on its Evidence panel, not closed.
        evidenceOnly: t.evidenceOnly ?? false,
        evidenceDetail: null,
        prdGenerating: false,
        evidenceGenerating: false,
        // Not persisted today (reopening hydrates the set from its
        // conversation), but restored when present so the id survives any
        // future persist — and so an ask can name the set without waiting on
        // that hydrate.
        ticketSetId: t.ticketSetId ?? null,
        // Persisted: preserves the inline-vs-header card ordering across reload.
        prdInFlow: t.prdInFlow ?? false,
        prdFlowTurnId: t.prdFlowTurnId,
      }))
    } catch { return [] }
  })
  // Ref kept in sync so callbacks can read current tabs without adding to deps
  const tabsRef = useRef<ChatTab[]>(tabs)
  tabsRef.current = tabs
  // Track which turn IDs have already been animated so re-mounting a tab doesn't
  // restart the typing animation from scratch.
  const animatedTurnIds = useRef<Set<string>>(new Set())
  const [activeTabId, setActiveTabId] = useState<string | null>(() => {
    try {
      const stored = sessionStorage.getItem(activeTabKey)
      // First load (no persisted active tab) → default to the pinned brief tab.
      // A persisted "" means the user was on the chat landing/new-chat (active
      // tab = null), so we honour that and DON'T fall back to the brief tab.
      if (stored == null) return BRIEF_TAB_ID
      return stored || null
    } catch { return BRIEF_TAB_ID }
  })
  // Mirror of activeTabId for async closures — a background PRD generation/load
  // only pushes its result into the shared content when its own tab is still
  // active, so it never stomps a tab the user has since switched to.
  const activeTabIdRef = useRef<string | null>(activeTabId)
  activeTabIdRef.current = activeTabId
  // The panel's live tab, for the async auto-opens: a render-time closure reads
  // whatever was open when the effect fired, which is the wrong answer several
  // hundred milliseconds later when a fetch comes back.
  const contentPanelTabRef = useRef(contentPanelTab)
  contentPanelTabRef.current = contentPanelTab
  // Which ticket set the SHARED panel is currently holding, for the tab-switch
  // reconcile. A ref rather than a dependency: taking `content` would re-run
  // that reconcile on every content write, when the only thing it reacts to is
  // the active tab changing.
  const ticketSetShownRef = useRef<{ id: number | null; busy: boolean }>({ id: null, busy: false })
  ticketSetShownRef.current = {
    id: content.ticketSet?.id ?? null,
    busy: !!content.ticketSetGenerating,
  }
  // True while this ChatScreen is mounted. Detached Ask polls read it to stop
  // (and LEAVE their persisted ask_id in place) when the user navigates to
  // another screen — so a background completion isn't dropped by a no-op state
  // write; the mount-time resume effect re-attaches and populates on return.
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])
  // When set, ChatScreen slides the content panel open on the NEXT commit, on
  // THIS tab — deferred one commit so the route-change panel-close (an artifact
  // opened from another surface routes to `/`) can't swallow it. The value is the
  // tab to land on: "prd" for every PRD open, "evidence" for a Top Insights
  // "View Evidence", which starts no PRD work.
  const [prdPanelPending, setPrdPanelPending] = useState<"evidence" | "prd" | "tickets" | null>(null)
  // Tab id of a chat just re-opened from history that owns a PRD — consumed by
  // the effect that opens its panel once that tab is the active one.
  const [resumePanelTabId, setResumePanelTabId] = useState<string | null>(null)

  // When the storage key changes (workspace switch OR a different user signs
  // in), reload tabs from the new user+company-scoped session storage so we
  // never show another tenant's — or another teammate's — chat threads.
  const prevTabsKeyRef = useRef(tabsKey)
  useEffect(() => {
    if (prevTabsKeyRef.current === tabsKey) return
    prevTabsKeyRef.current = tabsKey
    try {
      const saved = sessionStorage.getItem(tabsKey)
      if (saved) {
        setTabs((JSON.parse(saved) as Partial<ChatTab>[]).map((t) => ({
          id: t.id ?? "", title: t.title ?? "", thread: t.thread ?? [],
          dbConvId: t.dbConvId ?? null, briefMeta: t.briefMeta ?? null,
          insightBody: t.insightBody ?? null, prdId: t.prdId ?? null,
          prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
          evidenceOnly: t.evidenceOnly ?? false, evidenceDetail: null,
          prdInFlow: t.prdInFlow ?? false, prdFlowTurnId: t.prdFlowTurnId,
        })))
      } else {
        setTabs([])
      }
      const storedActive = sessionStorage.getItem(activeTabKey)
      // No persisted active tab for this company → default to the pinned brief
      // tab; a persisted "" honours the chat landing (active tab = null).
      setActiveTabId(storedActive == null ? BRIEF_TAB_ID : storedActive || null)
    } catch {
      setTabs([])
      setActiveTabId(BRIEF_TAB_ID)
    }
  }, [activeCompany, tabsKey, activeTabKey])

  // Persist tabs to sessionStorage (session-scoped; see the key comment above) —
  // strip large/transient fields (prd, evidence, *Generating)
  useEffect(() => {
    try {
      // prdCommandThinking is stripped with the rest: the in-flight call it
      // tracks does not survive a reload, so restoring it would leave a
      // thinking indicator spinning forever with nothing behind it. Turn-level
      // `partial` (live streamed answer text) is stripped for the same reason —
      // the resume path re-attaches the stream and rebuilds it from replay.
      // `evidenceDetail` is stripped as a large field like `prd`/`evidence`; the
      // small `evidenceOnly` flag beside it is what survives, and it's enough for
      // a reloaded tab to reopen on its Evidence panel (read-loaded by briefMeta).
      // The four ticket-set fields go with them, and the LATCH is the one that
      // matters: a persisted `ticketSetRunning` would come back as a disabled
      // "Writing tickets…" button for a run this page can no longer observe —
      // the stale in-flight state this feature must never show. The id/status
      // are dropped alongside it because the thread-resume probe
      // (GET /v1/ticket-sets/by-conversation) is the authority on both, and a
      // restored pair could contradict it.
      const slim = tabs.map(({ prd: _p, evidence: _e, evidenceDetail: _ed, prdGenerating: _pg, prdLoading: _pl, evidenceGenerating: _eg, hydrating: _h, prdCommandThinking: _pct, ticketSetRunning: _tsr, ticketSetId: _tsi, ticketSetStatus: _tss, ticketSetTask: _tst, ...rest }) => {
        // A still-pending summary indicator is dropped from the SAVED copy:
        // its in-flight call dies with the page, so restoring it would strand
        // a "Summarizing…" skeleton nothing will ever fill. The summary itself
        // persists (as the turn's reply, and as a conversation row) only once
        // it actually lands.
        const stripped = {
          ...rest,
          thread: rest.thread
            .filter((tn) => !(tn.summaryPending && !tn.reply))
            .map(({ partial: _partial, streamDropped: _sd, timedOut: _to, ...turn }) =>
              // An in-flight Slack send dies with the page, so `busy` must not
              // come back with it — a restored spinner would sit forever on a
              // share whose outcome nothing can now report. The preview and
              // the `resolved` record both persist (a settled share must still
              // say what was posted after a reload); only the in-flight flag
              // is dropped, exactly like `ticketSetRunning` above.
              turn.slackShare?.busy
                ? { ...turn, slackShare: { ...turn.slackShare, busy: false } }
                : turn),
        }
        // A PRD command awaiting its deferred reply (the clarify gate is still
        // deciding — see deferredAckRef) has a reply-less seed turn, and the
        // in-flight promise that would fill it dies with the page. Persisted
        // as-is, a reload restored it into "No response was generated for this
        // message." — false and a dead end. Mark it in the SAVED copy only, so
        // a restore can say what actually happened; a normal settle re-runs
        // this effect with the ref cleared and the mark comes straight off.
        if (!deferredAckRef.current.has(stripped.id)) return stripped
        const last = stripped.thread[stripped.thread.length - 1]
        if (!last || last.reply || last.error || last.stopped) return stripped
        return { ...stripped, thread: [...stripped.thread.slice(0, -1), { ...last, interrupted: true }] }
      })
      sessionStorage.setItem(tabsKey, JSON.stringify(slim))
    } catch { /* ignore */ }
  }, [tabs, tabsKey])
  useEffect(() => {
    try { sessionStorage.setItem(activeTabKey, activeTabId ?? "") } catch { /* ignore */ }
  }, [activeTabId, activeTabKey])

  // Remember the last CHAT tab (never the pinned brief tab, never the tab-less
  // landing) so "Workbench" can return the user to their open work. Written on
  // every switch; read only by the `?tab=last` handler below.
  useEffect(() => {
    if (!activeTabId || activeTabId === BRIEF_TAB_ID) return
    try { sessionStorage.setItem(lastTabKey, activeTabId) } catch { /* ignore */ }
  }, [activeTabId, lastTabKey])

  // The pinned brief tab is synthesized (not in `tabs`), so when it's active
  // `activeTab` is null. `isBriefTab` lets the render swap in <BriefChat/> for
  // the chat landing/thread + composer.
  const isBriefTab = activeTabId === BRIEF_TAB_ID
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null
  const thread = activeTab?.thread ?? []
  // The last turn a REPLY could still land on. A pending artifact-summary
  // placeholder is transparent here: it is appended the moment the artifact
  // lands, which would otherwise steal "last" from a genuinely in-flight ask
  // (flipping it to "No response was generated" mid-answer) and move the
  // artifact-action row onto a turn that has nothing to act on. Equals
  // `thread.length - 1` whenever no summary is pending.
  const lastLiveTurnIdx = (() => {
    for (let i = thread.length - 1; i >= 0; i--) {
      if (!(thread[i].summaryPending && !thread[i].reply)) return i
    }
    return thread.length - 1
  })()

  // ── Prototype map for the active tab's brief (one fetch per briefId) ───────
  const chatBriefId = activeTab?.briefMeta?.briefId ?? null
  const { entriesByInsight: chatEntriesByInsight, loading: chatMapLoading } =
    useBriefPrototypeMap(chatBriefId)

  const chatInsightState = useMemo(() => {
    if (!activeTab?.briefMeta) return null
    return prototypeStateForInsight(chatEntriesByInsight, activeTab.briefMeta.insightIndex)
  }, [activeTab?.briefMeta, chatEntriesByInsight])

  const setThread = useCallback((updater: ThreadTurn[] | ((prev: ThreadTurn[]) => ThreadTurn[])) => {
    setTabs((prev) => prev.map((t) => {
      if (t.id !== activeTabId) return t
      const next = typeof updater === "function" ? updater(t.thread) : updater
      return { ...t, thread: next }
    }))
  }, [activeTabId])

  // A "User input needed" answer patched the PRD (scoped edit). Refresh the
  // active tab's cached PRD + the shared content panel so the change shows live.
  const handleInputPrdUpdated = useCallback((prd: PrdState) => {
    setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prd } : t))
    setContent({ prd })
  }, [activeTabId, setContent])
  const [draft, setDraft] = useState("")
  // Per-tab busy tracking — a tab is "busy" while its own ask is in flight. The
  // composer's busy/disabled state is derived from the ACTIVE tab only (see the
  // `busy` const below `activeTab`), so switching to an idle tab shows an enabled
  // composer even while another tab is still loading.
  const [busyTabs, setBusyTabs] = useState<ReadonlySet<string>>(new Set())
  // The message the user just sent, rendered the INSTANT they hit send.
  //
  // Every send now opens with an awaited backend decision (POST /v1/chat/intent —
  // a full LLM round-trip) before ANY branch knows whether this message becomes a
  // chat turn or a command that seeds its own turn. That await used to sit in
  // front of every render, so the composer cleared into an empty screen for
  // multiple seconds and the send read as dropped. This is the bridge: the user's
  // words + a thinking skeleton, on screen on the send's own commit.
  //
  // Deliberately NOT a ThreadTurn and never persisted — the command flows
  // (openPrdInTab's seedTurn, seedCommandTurn) own the real turn they seed, and a
  // pre-rendered turn here would duplicate both the bubble and the Supabase row.
  // Whichever branch wins renders its real turn and clears this in the SAME
  // commit, so the handoff is invisible. `tabId` is the tab the send was aimed at
  // (null on the landing surface) so it only shows where it was typed.
  // `startedAt` is the wall clock of the send itself. It is handed to the real
  // turn when the dispatch settles, so the wait's elapsed-time ladder measures
  // ONE wait across the two mounts rather than restarting at the handoff.
  const [pendingSend, setPendingSend] = useState<
    { tabId: string | null; query: string; attachments: { name: string }[]; startedAt: number } | null
  >(null)
  // Insight keys ("briefId:insightIndex") known to already have a saved evidence
  // brief — flips the chat's first action to "View Evidence" (else it offers the
  // PRD). Populated per active insight via loadEvidenceByInsight (see effect below).
  const [insightsWithEvidence, setInsightsWithEvidence] = useState<ReadonlySet<string>>(new Set())
  const checkedEvidenceRef = useRef<Set<string>>(new Set())
  // Composer busy/disabled + "thinking" indicator reflect ONLY the active tab's
  // in-flight status. Another tab being mid-ask must not disable this composer.
  const busy = isComposerBusy(busyTabs, activeTabId)
  const [showSlash, setShowSlash] = useState(false)
  // The palette's entries — the company's own uploaded skills (PRD 1854).
  //
  // This used to be TWO lists merged at render time: the vendored built-in
  // catalog from `askApi.skills()` and the company's uploads from
  // `skillsApi.list()`. Chat no longer selects a built-in method for a turn, so
  // the built-in half would have offered ~78 triggers that resolve to nothing;
  // `askApi.skills()` now serves the company's own library and there is one
  // list again.
  const [skills, setSkills] = useState<SkillInfo[]>([])
  // Next-prompt suggestions, per tab. Fetched AFTER an answer settles, off the
  // answer path entirely: a slow, failed or never-returned request costs the
  // user nothing because the absence of suggestions is a normal, invisible
  // state (see components/shared/NextPromptSuggestions). Keyed by tab so a
  // background tab's suggestions never appear under another tab's thread, and
  // cleared the moment that tab sends again — stale chips proposing the
  // conversation the user has already moved past are worse than none.
  const [suggestionsByTab, setSuggestionsByTab] = useState<Record<string, string[]>>({})
  const clearSuggestions = useCallback((tabId: string) => {
    setSuggestionsByTab((prev) => (prev[tabId]?.length ? { ...prev, [tabId]: [] } : prev))
  }, [])
  const [slashFilter, setSlashFilter] = useState("")
  // Highlighted row in the slash palette (↑/↓ navigation, Enter selects).
  const [slashActive, setSlashActive] = useState(0)
  // The palette was opened from the `+` menu or ⌘/ rather than by typing "/".
  // Typing then must not slam it shut on the first keystroke, the way the
  // "draft no longer starts with /" rule does for a typed open.
  const [slashFromMenu, setSlashFromMenu] = useState(false)
  // A skill pinned onto the NEXT message. Selecting from the palette used to
  // paste "/competitive-intel " into the draft as raw text the user had to keep
  // intact; it is a removable chip now, and the trigger is re-attached to the
  // query at send time so the backend's deterministic slash fast-path is
  // unchanged.
  const [pinnedSkill, setPinnedSkill] = useState<PinnedSkill | null>(null)
  // The composer's `+` menu (Attach a file / Browse skills).
  const [plusMenuOpen, setPlusMenuOpen] = useState(false)
  const [plusMenuActive, setPlusMenuActive] = useState(0)
  // Transient composer hint line (role="status"), currently only the busy-Enter
  // answer. Auto-clears so it never becomes permanent chrome.
  const [composerHint, setComposerHint] = useState<"busy" | null>(null)
  const composerHintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const showComposerHint = useCallback((kind: "busy") => {
    setComposerHint(kind)
    if (composerHintTimerRef.current) clearTimeout(composerHintTimerRef.current)
    composerHintTimerRef.current = setTimeout(() => setComposerHint(null), BUSY_HINT_MS)
  }, [])
  useEffect(() => () => {
    if (composerHintTimerRef.current) clearTimeout(composerHintTimerRef.current)
  }, [])
  // Wall-clock start of each in-flight ask, keyed by turn id. A ref, not state:
  // the wait component owns its own tick, so this only has to be READ during
  // render — and it must survive the pending-send → real-turn handoff so the
  // rung ladder doesn't restart its clock halfway through one wait.
  const askStartRef = useRef<Map<string, number>>(new Map())
  // Turn ids whose ask was RE-ATTACHED (resumeAskGeneration) rather than POSTed
  // — the one thing that makes "Picking up where this left off" true.
  const resumedTurnsRef = useRef<Set<string>>(new Set())
  // Bumped whenever a resume re-attaches, purely to re-render the thread so the
  // wait picks the resumed copy up (the ref above carries no reactivity).
  const [, setResumeTick] = useState(0)
  // `file` is set for document formats (.pdf/.pptx/.docx/.doc): those can't be
  // inlined as text client-side. The File feeds the PRD-import command
  // ("import this as a PRD" → POST /v1/prd/import) or, for a plain question,
  // server-side text extraction at send time (POST /v1/ask/extract-file).
  const [attachments, setAttachments] = useState<{ name: string; content: string; file?: File }[]>([])
  // The attachment whose content is open in the viewer overlay (click a file
  // card on a user turn). Null = closed.
  const [viewerAttachment, setViewerAttachment] = useState<{ name: string; content: string; key?: string | null; mime?: string | null } | null>(null)
  // Per-tab in-flight guard — keyed by tabId. Prevents a tab from firing a second
  // ask while its own is still in flight, while letting OTHER tabs send concurrently.
  const askingTabsRef = useRef<Set<string>>(new Set())
  // Per-tab STOP flag — a tab id is present while the user has stopped its
  // in-flight ask. The ask poller reads this (isStopped) to bail; it's cleared
  // when a fresh ask starts on that tab so a stop never leaks into the next ask.
  const stoppedTabsRef = useRef<Set<string>>(new Set())
  const composerRef = useRef<HTMLTextAreaElement>(null)
  // Landing on a chat tab means you can just start typing. Selecting a tab — or
  // opening one with "+" — used to leave focus on the document body, so every
  // switch cost an extra click in the composer before the first keystroke.
  //
  // Deferred a frame ON PURPOSE. There is one <textarea> with two mount points
  // (the landing composer and the thread dock), and a tab switch can move it
  // between them or, coming from the pinned brief tab, mount it for the first
  // time — so the node `composerRef` holds when the click fires is often not the
  // one that ends up on screen. React flushes a click's state updates before the
  // next frame, so by the time this runs the ref points at the live composer.
  const focusComposerNextFrame = useCallback(() => {
    requestAnimationFrame(() => composerRef.current?.focus())
  }, [])
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Dictation ────────────────────────────────────────────────────────────
  // Whatever was already typed when the mic was switched on. Speech APPENDS to
  // a draft rather than replacing it, so half a typed question plus a spoken
  // finish is one question — and the hook hands back a cumulative transcript,
  // so this base is what makes assigning (rather than appending) safe as the
  // interim phrase rewrites itself word by word.
  const voiceBaseRef = useRef("")
  const handleVoiceTranscript = useCallback((text: string) => {
    if (!text) return
    setDraft((voiceBaseRef.current + text).slice(0, DRAFT_MAX_CHARS))
    // The textarea's auto-grow lives in the `change` handler, which speech never
    // fires — without this the box stays one line tall while the words pile up
    // out of sight.
    const ta = composerRef.current
    if (ta) {
      ta.style.height = "auto"
      ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`
    }
  }, [])
  const voice = useSpeechInput(handleVoiceTranscript)
  const handleToggleVoice = useCallback(() => {
    if (voice.listening) {
      voice.stop()
      composerRef.current?.focus()
      return
    }
    // Start speaking mid-sentence and the words join the sentence, with one
    // space between what was typed and what was said.
    const typed = draft.trimEnd()
    voiceBaseRef.current = typed ? `${typed} ` : ""
    voice.start()
  }, [voice, draft])

  // The scrolling thread viewport, so a new question (and the assistant's
  // thinking/answer under it) is scrolled into view instead of staying hidden
  // below the fold in a long conversation.
  const threadScrollRef = useRef<HTMLDivElement>(null)
  // Whether the user is pinned near the bottom. We only auto-follow streaming
  // replies while pinned, so scrolling up to read history isn't yanked back.
  const threadPinnedRef = useRef(true)
  const prevThreadLenRef = useRef(0)

  const scrollThreadToBottom = useCallback((behavior: ScrollBehavior) => {
    const el = threadScrollRef.current
    if (!el) return
    const jump = () => {
      const node = threadScrollRef.current
      if (!node) return
      try {
        node.scrollTo({ top: node.scrollHeight, behavior })
      } catch {
        // jsdom / older engines without Element.scrollTo — set position directly.
        node.scrollTop = node.scrollHeight
      }
    }
    // An instant jump lands on THIS frame: a send has to be on screen on its own
    // commit, not a frame later. The rAF pass then repeats it once the just-added
    // turn (and its thinking skeleton) is laid out, catching the height the first
    // call couldn't measure yet.
    if (behavior !== "smooth") jump()
    requestAnimationFrame(jump)
  }, [])

  // Track whether the user is pinned near the bottom of the thread. Auto-follow
  // only applies while pinned, so scrolling up to read earlier turns during a
  // long answer isn't fought by the follow effect.
  const handleThreadScroll = useCallback(() => {
    const el = threadScrollRef.current
    if (!el) return
    threadPinnedRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }, [])

  // Callback ref on the thread's content column. A ResizeObserver here keeps the
  // viewport pinned to the bottom as content GROWS — the thinking skeleton
  // appearing, then the answer typing in — not just on the initial render. That
  // covers the async growth a one-shot scroll misses. Re-attaches whenever the
  // content element mounts (tab switch, landing → thread), so it never observes
  // a stale node.
  const threadResizeObsRef = useRef<ResizeObserver | null>(null)
  const setThreadContentEl = useCallback((el: HTMLDivElement | null) => {
    threadResizeObsRef.current?.disconnect()
    threadResizeObsRef.current = null
    if (!el || typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver(() => {
      const scroller = threadScrollRef.current
      if (scroller && threadPinnedRef.current) scroller.scrollTop = scroller.scrollHeight
    })
    ro.observe(el)
    threadResizeObsRef.current = ro
  }, [])
  useEffect(() => () => threadResizeObsRef.current?.disconnect(), [])

  // The send itself is what has to move the viewport. `pendingSend` renders the
  // user's message on the send's own commit — seconds before the real turn lands
  // (the intent decision is a round-trip away), so a scroll keyed on thread
  // growth left the message parked below the fold that whole time. Re-pin and
  // JUMP: from far up a long thread a smooth animation both takes too long and
  // un-pins the follow behavior on its own way down (the scroll handler samples
  // it mid-animation, well short of the bottom), which is exactly how the answer
  // then streamed in off-screen.
  useEffect(() => {
    if (!pendingSend || pendingSend.tabId !== activeTabId) return
    threadPinnedRef.current = true
    scrollThreadToBottom("auto")
  }, [pendingSend, activeTabId, scrollThreadToBottom])

  // A new turn (the user just asked, or a command seeded its own turn) → re-pin
  // and jump so the question + the assistant's thinking sit in view; the
  // ResizeObserver then follows the answer as it grows. Guard on a real length
  // increase so a reply landing on an existing turn doesn't double-trigger (the
  // observer already handles growth).
  useEffect(() => {
    if (thread.length > prevThreadLenRef.current) {
      threadPinnedRef.current = true
      scrollThreadToBottom("auto")
    }
    prevThreadLenRef.current = thread.length
  }, [thread.length, scrollThreadToBottom])

  // On tab switch/open, land at the bottom (newest turn) without animation and
  // reset the pinned state for the newly shown thread.
  useEffect(() => {
    prevThreadLenRef.current = thread.length
    threadPinnedRef.current = true
    scrollThreadToBottom("auto")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, scrollThreadToBottom])

  // Attach: documents keep the real File (for the PRD-import command); plain-text
  // formats are read as text and inlined into the next ask as context.
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    Array.from(files).forEach((file) => {
      if (/\.(pdf|pptx|docx|doc)$/i.test(file.name)) {
        setAttachments((prev) => [...prev, { name: file.name, content: "", file }])
        return
      }
      const reader = new FileReader()
      reader.onload = () => {
        const content = reader.result as string
        // Keep the raw File on text attachments too — the original bytes are
        // uploaded on send so the chip can render/download the real file later.
        setAttachments((prev) => [...prev, { name: file.name, content: content.slice(0, 50000), file }])
      }
      reader.readAsText(file)
    })
    e.target.value = "" // reset so same file can be re-selected
  }, [showToast])

  // Load the palette on mount.
  //
  // A failure leaves it EMPTY rather than falling back to a hardcoded list.
  // The old fallback named nine built-in triggers (`/prd`, `/prioritize`,
  // `/compete`, …) and would now be nine dead entries — a palette that offers
  // a trigger the backend will not honour is worse than an empty one, which is
  // also exactly what a company with no uploads correctly sees.
  useEffect(() => {
    askApi.skills().then((r) => setSkills(r.skills)).catch(() => setSkills([]))
  }, [])

  // Create a new tab or, if a tab with the same title already exists, switch to it
  const openTab = useCallback((title: string, initialThread?: ThreadTurn[], dbId?: number | null, briefMeta?: BriefMeta | null) => {
    const existing = tabsRef.current.find((t) => t.title === title)
    if (existing) {
      setActiveTabId(existing.id)
      setDraft("")
      return existing.id
    }
    const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    setTabs((prev) => [...prev, {
      id, title, thread: initialThread ?? [], dbConvId: dbId ?? null,
      briefMeta: briefMeta ?? null, insightBody: null, prd: null, prdId: null, evidence: null,
      prdGenerating: false, evidenceGenerating: false,
    }])
    setActiveTabId(id)
    setDraft("")
    return id
  }, [])

  const closeTab = useCallback((tabId: string) => {
    const next = tabsRef.current.filter((t) => t.id !== tabId)
    setTabs(next)
    // Closing the ACTIVE tab hands focus to the last surviving chat tab; when
    // none remain, the pinned Top Insights tab becomes active — never the
    // tab-less landing (which left NO tab looking active in the strip).
    if (activeTabIdRef.current === tabId) {
      setActiveTabId(next.length > 0 ? next[next.length - 1].id : BRIEF_TAB_ID)
    }
  }, [])

  // Rehydrate a PRD tab's chat thread from its saved conversation. A PRD's chat
  // is keyed by prd_id in Supabase (conversationsApi.byPrd), so reopening a PRD —
  // even on a new device or after the localStorage tab is gone — restores the
  // user's earlier questions + Sprntly's answers instead of an empty thread. Only
  // ever fills a still-empty, unconverted tab (guarded again inside the setter so
  // a race with live typing can't clobber it); non-fatal on any failure.
  const hydratePrdThread = useCallback(async (tabId: string, prdId: number) => {
    // A just-created tab may not be in tabsRef yet (state not flushed), so DON'T
    // bail when it's missing — only when it's present AND already has content. The
    // setTabs guard below re-checks, so a genuinely absent tab is a harmless no-op.
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (tab && (tab.thread.length > 0 || tab.dbConvId != null)) return
    try {
      const { conversationsApi } = await import("../../../lib/api")
      const { conversation, turns } = await conversationsApi.byPrd(prdId)
      if (!conversation || turns.length === 0) return
      const restored: ThreadTurn[] = []
      for (let i = 0; i < turns.length; i++) {
        const t = turns[i]
        if (t.role === "user") {
          const next = turns[i + 1]
          const reply = next?.role === "assistant"
            ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
            : undefined
          restored.push({
            id: `prdhist-${conversation.id}-${i}`,
            query: t.content,
            reply,
            // Carry the file chip (with its storage key) so a reopened import-PRD
            // chat can still render/download the original document.
            ...(t.attachments?.length ? { attachments: t.attachments } : {}),
          })
          if (reply) i++
        } else if (t.role === "assistant" && t.content.trim()) {
          // Unconsumed assistant row (the artifact summary posted after the
          // ack) → agent-only turn, mirroring buildRestored. Dropping it made
          // the summary vanish from every reopened PRD chat.
          restored.push({
            id: `prdhist-${conversation.id}-${i}`,
            query: "",
            reply: { answer: t.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse,
          })
        }
      }
      if (restored.length === 0) return
      setTabs((prev) => prev.map((t) =>
        t.id === tabId && t.thread.length === 0 && t.dbConvId == null
          ? { ...t, thread: restored, dbConvId: conversation.id }
          : t))
    } catch { /* non-fatal: fall back to an empty thread */ }
  }, [])

  // The open-path prd_id (source ready/load, or a generate that resolves) is
  // unreliable: "View PRD" degrades to a generate/find-or-create when the
  // insight→PRD map hasn't populated yet, so the tab's prdId can stay null. But
  // chatInsightState resolves the ACTIVE tab's real PRD id from that same map,
  // keyed by briefMeta — and it lands reliably once the map loads, independent of
  // the open path. So whenever we know the active tab's prd_id: (1) backfill
  // tab.prdId (null only) so chat persistence stamps the right PRD, and (2)
  // rehydrate the saved chat (guarded to an empty, unconverted tab). This is what
  // makes a reopened PRD actually restore its prior questions.
  const resolvedInsightPrdId = chatInsightState?.hasPrd ? chatInsightState.prdId : null
  useEffect(() => {
    if (resolvedInsightPrdId == null || activeTabId == null) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab) return
    // …but an EVIDENCE tab is not a PRD tab. It was opened for a finding, and the
    // insight may well have a PRD in the DB — adopting that id here would make the
    // tab claim a document it never opened, so refocusing it would restore that
    // PRD instead of its evidence. It becomes a PRD tab only when one actually
    // lands in it (the panel's PRD tab resolving one, which stamps `prdId`).
    if (tab.evidenceOnly && tab.prd == null && tab.prdId == null) return
    if (tab.prdId == null) {
      setTabs((prev) => prev.map((t) =>
        t.id === activeTabId && t.prdId == null ? { ...t, prdId: resolvedInsightPrdId } : t))
    }
    if (tab.thread.length === 0 && tab.dbConvId == null) {
      void hydratePrdThread(activeTabId, resolvedInsightPrdId)
    }
  }, [activeTabId, resolvedInsightPrdId, hydratePrdThread])

  // The DB conversation id for a command-seeded PRD tab, promised per tab.
  // `seedCommandTurn` registers it (synchronously, via
  // persistence.ensureConversation) the instant a command opens a tab; the
  // import/generate call inside `openPrdInTab` awaits it and hands the id to
  // the backend, which binds conversation → PRD itself.
  //
  // Why this exists: the conversation row is necessarily created BEFORE the
  // prd_id is known, and the client used to back-patch the link from a React
  // effect. Navigating away mid-generation unmounted that effect, so the chat
  // kept prd_id=NULL and came back from history with no PRD attached at all.
  // The backend can't lose the link that way. Best-effort throughout: no id (or
  // a slow create) just falls back to the client back-patch below.
  const seedConvIdRef = useRef<Map<string, Promise<number | null>>>(new Map())

  // ── Deferred command acknowledgment (clarify-first) ────────────────────────
  // "Generate a PRD for X" may be answered with QUESTIONS instead of a document,
  // so its acknowledgment ("Generating a PRD for that — it'll open in the panel
  // on the right…") can't be written when the command is seeded: half the time
  // it turns out to be false, and it sat above the questions claiming a PRD was
  // on its way while the agent was still asking what to build. The seed now
  // leaves the reply EMPTY (the thinking indicator carries that window) and the
  // gate's outcome decides what lands on the turn — the ack, or the questions.
  //
  // This holds the rail/DB turn id the deferred write needs. Keyed by tab;
  // seeded synchronously by seedCommandTurn, consumed one network round-trip
  // later inside openPrdInTab's async block, so the race is never live.
  // ONE entry per tab is an invariant, not an accident: every generateTask
  // command enters through submitAsk, whose prdCommandThinking guard holds any
  // send on this tab until the gate settles — so a second command can never
  // overwrite a live entry and cross-wire the two replies.
  const deferredAckRef = useRef<Map<string, { turnId: string; req: LocalPrdTabRequest }>>(new Map())
  // finalizeConversationTurn is declared further down (it depends on state
  // helpers defined after openPrdInTab), so openPrdInTab reaches it through a
  // ref rather than a closure it cannot legally capture at render time.
  const finalizeTurnRef = useRef<
    ((turnId: string, updates: { reply?: AskResponse; error?: string }, targetTabId: string) => void) | null
  >(null)
  // postArtifactSummary is likewise declared after the generation flows that
  // call it (it depends on `persistence`), so completion sites reach it through
  // this ref — assigned right after its definition, consumed only in async
  // completion handlers, so it is never read before assignment.
  const postSummaryRef = useRef<
    ((tabId: string, kind: "prd" | "evidence" | "prototype" | "ticket_set", artifactId: number) => void) | null
  >(null)
  /** Write the reply the clarify gate settled on — the ack, or the questions —
   *  onto the command turn's thread entry AND its conversation row. No-op when
   *  the command wasn't seeded (a header open, or a re-issued command with no
   *  seed query), which is exactly when there's no turn to answer. */
  const settleCommandAck = useCallback((tabId: string, seedTurnId: string, reply: AskResponse, clarify?: ClarifyQuestion[]) => {
    setTabs((prev) => prev.map((t) => t.id === tabId
      ? {
          ...t,
          thread: t.thread.map((tn) =>
            tn.id === seedTurnId ? { ...tn, reply, ...(clarify ? { clarify } : {}) } : tn),
        }
      : t))
    const deferred = deferredAckRef.current.get(tabId)
    if (!deferred) return
    deferredAckRef.current.delete(tabId)
    finalizeTurnRef.current?.(deferred.turnId, { reply }, tabId)
  }, [])
  /** The command died before the gate could settle it (generate POST failed, or
   *  came back unavailable). A deferred ack that is never written leaves the turn
   *  reading "No response was generated for this message." — so say what actually
   *  happened instead. No-op once the ack or the questions have already landed. */
  const failDeferredAck = useCallback((tabId: string, seedTurnId: string | undefined, message: string) => {
    if (!seedTurnId || !deferredAckRef.current.has(tabId)) return
    const deferred = deferredAckRef.current.get(tabId)!
    const turnId = deferred.turnId
    deferredAckRef.current.delete(tabId)
    const detail = message.trim().slice(0, 200)
    // The verb has to match what was actually attempted. An OPEN that fails
    // ("PRD isn't ready yet" from loadPrdById) reported as "I couldn't start
    // that PRD" describes a generation the user never asked for — the same
    // class of false statement the deferral exists to prevent.
    const verb = deferred.req.source.kind === "load" ? "open" : "start"
    const lead = `I couldn't ${verb} that PRD`
    setTabs((prev) => prev.map((t) => t.id === tabId
      ? {
          ...t,
          thread: t.thread.map((tn) => tn.id === seedTurnId
            ? { ...tn, error: detail ? `${lead} — ${detail}` : `${lead}.` }
            : tn),
        }
      : t))
    finalizeTurnRef.current?.(turnId, { error: detail }, tabId)
  }, [])

  // Bind a tab's chat conversation to the PRD it just started, for the case the
  // conversation didn't exist yet when the generate call went out (a brand-new
  // command tab). Runs inside the generate promise chain — NOT a React effect —
  // so it still completes after the user navigates away, which is precisely when
  // the old effect-based back-patch was lost. The write itself is the backend's
  // (ownership-checked PATCH); we only supply the pairing.
  const bindConvToPrd = useCallback(async (tabId: string, prdId: number) => {
    try {
      const pending = seedConvIdRef.current.get(tabId)
      const convId = pending
        ? await pending
        : tabsRef.current.find((t) => t.id === tabId)?.dbConvId ?? null
      if (convId == null) return
      const { conversationsApi } = await import("../../../lib/api")
      await conversationsApi.update(convId, { prd_id: prdId })
    } catch {
      // Non-fatal: the tabs effect below re-attempts while the screen is mounted.
    }
  }, [])

  // ── Open a PRD as a NEW CHAT TAB with the content panel over it ─────────────
  // A "view/generate PRD" from another surface (brief cards, brief composer,
  // backlog) routes here via NavigationContext.openPrdTab → pendingPrdTab. We
  // spawn (or reuse, by title) a fresh chat tab, drive the requested source into
  // its cached PRD + the shared ContentContext, and flag the content panel to
  // slide open (deferred a commit so the route-change close can't swallow it).
  // The PRD/Evidence/Tickets all render in that panel — the tab itself is a
  // normal chat the user can keep talking in. Returns the target tab's id so
  // the consumer can persist a seeded command turn against it.
  const openPrdInTab = useCallback((req: LocalPrdTabRequest): string => {
    const { title, source } = req
    const meta = source.kind === "generateIdeation" || source.kind === "importDoc" || source.kind === "generateTask"
      ? null : source.meta
    // Same-tab generation: a pinned `inTabId` (a PRD command typed in a plain
    // chat tab) reuses THAT tab, so the PRD lands beside the conversation that
    // motivated it. Otherwise fall back to the title-match reuse (a re-issued
    // command) or a fresh tab.
    // Reuse order: the pinned tab, then the tab already HOLDING this PRD, then
    // a title match. The prd-id pass matters for `load` — a `?prd=` deep link
    // (and a reload of one) always arrives with the generic title "PRD", which
    // never matches the real tab's "PRD · <name>", so title-matching alone
    // spawned a SECOND tab for a PRD that was already open.
    //
    // …and a `load` STOPS there: it always knows its prd id, so a title match
    // can only ever be a DIFFERENT document that happens to share a name. That
    // is not hypothetical here — same-titled PRDs are precisely what the open
    // flow's disambiguation surfaces as chips, so "open 2216, then click the
    // chip for the other Compliance Reporting" would have matched 2216's tab by
    // title, found its cached `prd`, and shown 2216 while the user asked for
    // 2214, with nothing signalling the substitution. #1039's lesson exactly:
    // any dedupe keyed on a display string breaks as soon as two entries share
    // one. Key on the identifier, and where there is an identifier, do not fall
    // back to the string at all.
    const existing = (req.inTabId ? tabsRef.current.find((t) => t.id === req.inTabId) : undefined)
      ?? (source.kind === "load"
        ? tabsRef.current.find((t) => t.prdId === source.prdId)
        : undefined)
      ?? (source.kind === "load"
        ? undefined
        : tabsRef.current.find((t) => t.title === title))
    const tabId = existing?.id ?? `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    // A command phrasing opened this tab ("convert this PRD into tickets",
    // "generate a PRD"): seed the thread with the user's message + an
    // acknowledgment, so the chat shows WHY a generation is running instead of
    // sitting empty next to the spinning panel.
    // …EXCEPT for "generate a PRD for X", where the acknowledgment is deferred
    // until the clarify gate says which response the user is actually getting
    // (see deferredAckRef). Its reply-less turn shows the thinking indicator for
    // that window and is filled in by settleCommandAck.
    //
    // The in-chat OPEN command ("open the PRD for X" — a `load` carrying a seed
    // query) defers for the same reason, one step earlier in the same lesson:
    // "Opening that PRD in the panel on the right" is a claim about something
    // that has not happened yet. A resolved candidate is only an ID, and
    // loadPrdById still says no to a PRD mid-regeneration — which left that
    // sentence in the thread, and in the persisted conversation, next to a
    // panel that never opened. Now the ack is written only once the document is
    // actually on screen (settleCommandAck below), and a refusal writes what
    // really happened (failDeferredAck).
    // …but NOT when the document is already cached on the target tab: that open
    // returns below without ever entering the async block, so a deferred ack
    // would have nothing to settle it. `ackInline` is the caller's verdict on
    // exactly that, shared with seedCommandTurn so the two cannot disagree.
    const deferAck =
      source.kind === "generateTask" ||
      (source.kind === "load" && !!req.seedQuery && !req.ackInline)
    const seedTurn: ThreadTurn | null = req.seedQuery
      ? {
          id: `seed-${Date.now()}`,
          query: req.seedQuery,
          ...(deferAck ? {} : { reply: commandAckReply(req) }),
          // "convert this document into a PRD": the attached file IS the subject
          // of the command, so show it as a chip on the user's turn (matching a
          // plain attachment send) rather than only inside the panel.
          ...(source.kind === "importDoc" ? { attachments: [{ name: source.file.name }] } : {}),
        }
      : null
    // Was this tab opened by an IN-CHAT COMMAND (its first turn is the user's
    // command) rather than as a header from a brief insight / ideation idea /
    // backlog load? Only the fix-#1 command kinds — doc import, "generate a PRD
    // for X" (generateTask, or BriefChat's resume+seedQuery command) — flow the
    // insight card + questions INLINE below the command turn. generateIdeation
    // carries a seedQuery too but is a HEADER open (its framing card stays on
    // top), so it's deliberately excluded here. See the render block below.
    // A `load` carrying a seedQuery is the in-chat OPEN command ("open the PRD
    // for X") — the same shape as the other command kinds, so its PRD card
    // belongs inline under that command turn. A load WITHOUT one is a deep link
    // / reload, which stays a header open exactly as before.
    const prdInFlow =
      source.kind === "importDoc" ||
      source.kind === "generateTask" ||
      ((source.kind === "resume" || source.kind === "load") && !!req.seedQuery)
    if (existing) {
      setActiveTabId(existing.id)
      // Backfill the insight body onto an already-open tab that lacks one (e.g. a
      // tab created before this field existed, or opened via a path that didn't
      // carry it) so reopening the insight surfaces its content, not just a title.
      // A re-issued command appends its turn to the existing thread.
      if ((req.insightBody && !existing.insightBody) || seedTurn) {
        setTabs((prev) => prev.map((t) => t.id === existing.id ? {
          ...t,
          insightBody: t.insightBody ?? req.insightBody ?? null,
          thread: seedTurn ? [...t.thread, seedTurn] : t.thread,
          // Never downgrade a header tab to in-flow, but a command re-issued on a
          // command tab keeps it in-flow.
          prdInFlow: t.prdInFlow || prdInFlow,
          // Anchor the inline PRD card to THIS command turn — in a reused chat
          // tab the command lands mid-thread, not at thread[0].
          ...(seedTurn && prdInFlow ? { prdFlowTurnId: seedTurn.id } : {}),
        } : t))
      }
    } else {
      setTabs((prev) => [...prev, {
        id: tabId, title, thread: seedTurn ? [seedTurn] : [], dbConvId: null, briefMeta: meta,
        insightBody: req.insightBody ?? null, prdId: null,
        prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
        prdInFlow,
        ...(seedTurn && prdInFlow ? { prdFlowTurnId: seedTurn.id } : {}),
      }])
      setActiveTabId(tabId)
    }
    setDraft("")
    // A "generate a PRD for X" command may be answered with QUESTIONS rather
    // than a document — the clarify-first gate runs before any generation.
    // Opening the rail now would park an EMPTY PRD panel beside those questions,
    // and an empty panel is exactly what used to get filled with the workspace's
    // most recent (unrelated) PRD, reading as if it were the answer. So this one
    // kind waits: the panel opens the moment generation actually starts, either
    // straight after the gate passes (below) or when the user answers.
    const clarifyFirst = source.kind === "generateTask"
    if (!clarifyFirst) setPrdPanelPending(source.kind === "evidence" ? "evidence" : "prd")

    // ── Evidence-first open ──────────────────────────────────────────────────
    // A Top Insights card's "View Evidence" opens the finding as its own chat tab
    // with the panel on Evidence — the same slide-in every PRD gets, just landed
    // on a different tab. NO PRD work starts here: the PRD tab in that panel
    // resolves one on demand (find-or-create) if the user asks for it.
    //
    // `detail` scopes ContentPanel's Evidence tab to this insight (that tab owns
    // the load/generate). `prdMeta` is set alongside so the panel's PRD tab knows
    // which insight to resolve, and `prd` is cleared unless this very tab already
    // holds one — the panel is global, so another PRD tab's document must never
    // linger over this finding.
    if (source.kind === "evidence") {
      setTabs((prev) => prev.map((t) => t.id === tabId
        ? { ...t, evidenceOnly: true, evidenceDetail: source.detail }
        : t))
      setContent({
        detail: source.detail,
        prd: existing?.prd ?? null,
        prdMeta: source.meta,
        prdGenerating: false,
        prdPartialHtml: null,
      })
      return tabId
    }

    // Reopening an EXISTING PRD (ready | load)? Rehydrate its saved chat thread by
    // prd_id so the user's prior questions come back. New PRDs (generate*) have no
    // prior conversation, so we skip — their prd_id is stamped on first send.
    const knownPrdId = source.kind === "ready" ? source.prd.prd_id
      : source.kind === "load" ? source.prdId
      : source.kind === "resume" ? source.prdId
      : null
    if (knownPrdId != null) void hydratePrdThread(tabId, knownPrdId)
    // "convert this PRD into tickets" — the resume/importDoc paths land the panel
    // on the Tickets tab (and kick user-stories generation) once the PRD is ready.
    const wantsTickets = (source.kind === "resume" || source.kind === "importDoc") && !!source.openTickets

    // Reuse a PRD already cached on this tab (unless the caller handed us a fresh
    // one) — don't regenerate/re-fetch an already-open PRD.
    if (existing?.prd && source.kind !== "ready") {
      setContent({ prd: existing.prd, prdMeta: existing.briefMeta, prdGenerating: false })
      // No ack settling here, deliberately. This return is upstream of the turn
      // ever being registered (seedCommandTurn runs after openPrdInTab), so a
      // settle would write the thread and skip persistence entirely. `ackInline`
      // has already told the seed turn to carry its reply, so the acknowledgment
      // is on screen AND in the conversation before we get here.
      return tabId
    }
    // Caller already holds the PRD — show it immediately, no async work.
    if (source.kind === "ready") {
      setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: source.prd, prdId: source.prd.prd_id, briefMeta: source.meta } : t))
      setContent({ prd: source.prd, prdMeta: source.meta, prdGenerating: false })
      return tabId
    }
    // generate | generateIdeation | load | resume — kick off, show the panel's
    // spinner, then land the result on the tab (and shared content while active).
    // When the prd_id is known UPFRONT (load/resume — incl. chat-task PRDs whose
    // generation was kicked before this tab opened), stamp it on the tab NOW,
    // not only on success: `prdId` is what survives the sessionStorage
    // round-trip, so a reload mid-generation can find and resume the run
    // instead of orphaning it client-side.
    setTabs((prev) => prev.map((t) => t.id === tabId
      ? {
          ...t, prd: null, prdId: knownPrdId ?? t.prdId, briefMeta: meta,
          // `clarifyFirst` shows a thinking indicator in the CHAT instead of a
          // generating rail, since we don't yet know which of the two the agent
          // is about to produce.
          ...(clarifyFirst ? { prdCommandThinking: true } : { prdGenerating: true }),
        }
      : t))
    // `clarifyFirst` holds the generating state back too — the card would
    // otherwise read "Generating PRD…" while the agent is still asking what to
    // build. Both flip on together once the gate passes.
    if (!clarifyFirst) setContent({ prd: null, prdMeta: meta, prdGenerating: true, prdPartialHtml: null })
    void (async () => {
      // Live preview: forward the accumulating Part A HTML (throttled inside
      // runPrdGeneration) into shared content so PrdPanelContent renders the
      // draft as it streams — only while this tab is still the active one.
      const onPartial = (html: string) => {
        if (activeTabIdRef.current === tabId) setContent({ prdPartialHtml: html })
      }
      // Captured inside the importDoc/generateTask branch below (the only
      // kinds that auto-fork a project) and read in the success block to
      // navigate the user into the new project's private chat.
      let autoProjectId: number | null = null
      try {
        const result =
          source.kind === "generate" ? await runPrdGeneration(source.meta, onPartial)
          : source.kind === "generateIdeation" ? await runPrdGenerationFromIdeation(source.ideationItemId, onPartial)
          : source.kind === "resume" ? await resumePrdGeneration(source.prdId, source.meta ?? undefined, onPartial)
          // importDoc | generateTask: the seed turn + generating skeleton are
          // ALREADY on screen (rendered synchronously above). Only NOW do we hit
          // the network — this ordering is the entire reason these command flows
          // route through openPrdInTab instead of the caller awaiting
          // importDoc/generateFromTask first (which cleared the composer and left
          // the chat empty for the multi-second call, reading as a frozen app).
          // The POST returns a generating prd_id we poll to ready via the SAME
          // resume machinery used everywhere else.
          : source.kind === "importDoc" || source.kind === "generateTask"
          ? await (async () => {
              const { prdApi } = await import("../../../lib/api")
              if (source.kind === "generateTask") {
                // Clarify-first gate (issue d): ALWAYS check sufficiency before
                // generating — even a detailed-looking prompt can be missing
                // users or success criteria. Fail-open on any error so the gate
                // can never block a PRD. Insufficient → park the task on the
                // tab, post the questions into its thread, and stop here; the
                // user's next message in this tab carries the answers.
                const verdict = await Promise.resolve()
                  .then(() => prdApi.clarifyTask(source.task, source.sourceDocs))
                  .catch(() => ({ sufficient: true, questions: [], missing: [] }))
                if (!verdict.sufficient && verdict.questions.length) {
                  // A command with no seed turn (opened from a button rather
                  // than typed) has nothing to answer, so the questions arrive
                  // as their own agent-only turn. Either way ONE turn carries
                  // them, and `pendingClarify` names it.
                  const orphanTurnId = `clarify-${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : Date.now()}`
                  const clarifyTurnId = seedTurn?.id ?? orphanTurnId
                  setTabs((prev) => prev.map((t) => t.id === tabId
                    ? {
                        ...t,
                        prdGenerating: false,
                        // The questions ARE the response — indicator off.
                        prdCommandThinking: false,
                        pendingClarify: { task: source.task, sourceDocs: source.sourceDocs, turnId: clarifyTurnId },
                        // The questions ANSWER the command turn — they are not a
                        // second message under a stale "generating…" ack. Landing
                        // them on that turn is also what makes the pair survive a
                        // reload: history rebuilds as user→assistant, and the
                        // orphaned agent-only turn had nowhere to go.
                        ...(seedTurn ? {} : { thread: [...t.thread, {
                          id: orphanTurnId,
                          query: "",
                          reply: clarifyQuestionsReply(verdict.questions),
                          clarify: verdict.questions,
                        }] }),
                      }
                    : t))
                  if (seedTurn) {
                    settleCommandAck(tabId, seedTurn.id, clarifyQuestionsReply(verdict.questions), verdict.questions)
                  }
                  if (activeTabIdRef.current === tabId) {
                    setContent({ prdGenerating: false, prdPartialHtml: null })
                  }
                  return { ok: false as const, message: "", clarify: true }
                }
                // Sufficient (or the gate failed open): generation starts NOW,
                // so this is the moment the rail earns its place on screen —
                // still before the generate POST, so the spinner is optimistic
                // exactly as it is for every other command kind.
                // The generating rail + card take over the "working" signal here,
                // so the chat indicator hands off rather than stacking.
                setTabs((prev) => prev.map((t) => t.id === tabId
                  ? { ...t, prdGenerating: true, prdCommandThinking: false } : t))
                // …and NOW the acknowledgment is true, so it lands on the command
                // turn (thread + conversation row) — the write the seed deferred.
                if (seedTurn) settleCommandAck(tabId, seedTurn.id, commandAckReply(req))
                if (activeTabIdRef.current === tabId) {
                  setContent({ prd: null, prdMeta: meta, prdGenerating: true, prdPartialHtml: null })
                  setPrdPanelPending("prd")
                }
              }
              // This chat's DB conversation, IF it already has one (a command
              // issued in an existing chat). Read synchronously — the POST must
              // never wait on persistence: gating it on the conversation create
              // delayed the whole generation behind a round-trip, which is the
              // latency bug this flow exists to avoid. A brand-new command tab
              // has no id yet and binds a moment later (bindConvToPrd below).
              const knownConvId = tabsRef.current.find((t) => t.id === tabId)?.dbConvId ?? null
              const start = source.kind === "importDoc"
                ? await prdApi.importDoc(
                    source.file, source.company, knownConvId,
                    source.artifactTemplateId,
                  )
                : await prdApi.generateFromTask(
                    source.task, false, source.sourceDocs, knownConvId,
                    source.artifactTemplateId,
                  )
              // The project this chat was auto-forked into (server-side) — read
              // in the success block below to land the user in its private chat.
              autoProjectId = start.project_id ?? null
              // Not bound at creation? Bind now, from THIS promise chain rather
              // than a React effect — the chain outlives the screen, so leaving
              // the page mid-generation no longer orphans the chat from its PRD.
              if (knownConvId == null) void bindConvToPrd(tabId, start.prd_id)
              // Stamp the now-known prd_id immediately (as the resume path does
              // upfront) so a reload past this point can find + resume the run,
              // and adopt the backend's cleaner title over the placeholder.
              setTabs((prev) => prev.map((t) => t.id === tabId
                ? {
                    ...t,
                    prdId: start.prd_id,
                    title: start.title
                      ? (source.kind === "generateTask" ? `PRD · ${start.title}` : start.title)
                      : t.title,
                  }
                : t))
              return resumePrdGeneration(start.prd_id, undefined, onPartial)
            })()
          : await loadPrdById(source.prdId)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false } : t))
          if (activeTabIdRef.current === tabId) setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false, prdPartialHtml: null })
          // "convert this PRD into tickets": the user asked for TICKETS — once
          // the imported PRD is ready, kick the user-stories generation NOW
          // (fire-and-forget; the backend dedups in-flight jobs) so work starts
          // before the Tickets tab even mounts and does its cache-read→generate
          // round-trip. The tab's own poll picks the job up.
          if (wantsTickets) {
            void storiesApi.generate(result.prd.prd_id).catch(() => {})
          }
          // …then switch the panel to the Tickets tab. Only while this tab is
          // still active — never yank the panel out from under another tab.
          //
          // `setPrdPanelPending(null)` FIRST, and it is load-bearing. Opening a
          // PRD tab arms a DEFERRED panel-open (`prdPanelPending`, drained by an
          // effect a commit later) so the panel survives the route change that
          // openPrdTab triggers. That deferred open lands on "prd" — and if it
          // is still armed when we get here, it fires AFTER this line and puts
          // the panel straight back on the PRD, silently undoing the switch the
          // user actually asked for.
          //
          // Whether it has drained yet is a race between a React commit and the
          // import + generation round trip, so it resolves one way against a
          // real network and the other against a fast or cached import. Cancel
          // it instead: by this point the destination is decided, and a default
          // that arrived earlier has no business overriding it.
          if (wantsTickets && activeTabIdRef.current === tabId) {
            setPrdPanelPending(null)
            openContentPanel("tickets")
          }
          // The prd_id was UNKNOWN upfront (generate | generateIdeation — including
          // "View PRD" find-or-create, which resolves an EXISTING PRD). Now that we
          // have it, rehydrate the tab's chat by prd_id. New PRDs return no
          // conversation (no-op); an existing one restores the user's prior turns.
          // The upfront ready/load path already hydrated, so skip those here.
          if (knownPrdId == null) void hydratePrdThread(tabId, result.prd.prd_id)
          // Chat summary of what got built — only for kinds that carry EXPLICIT
          // generation intent (a typed command, a doc conversion, an ideation
          // framing). Bare `generate` is excluded: its find-or-create regularly
          // resolves an existing PRD on a "View PRD" click, and a reopen must
          // never re-summarize. resume/ready/load are reopens by definition.
          if (
            source.kind === "generateTask" ||
            source.kind === "importDoc" ||
            source.kind === "generateIdeation"
          ) {
            postSummaryRef.current?.(tabId, "prd", result.prd.prd_id)
          }
          // The OPEN command's deferred ack, settled the moment the claim it
          // makes became true: the document is loaded and the panel is showing
          // it. Anything earlier is a promise, and this one had a real way of
          // going unkept (a PRD mid-regeneration refuses to load).
          if (deferAck && source.kind === "load" && seedTurn) {
            settleCommandAck(tabId, seedTurn.id, commandAckReply(req))
          }
          // The PRD came from the main chat and forked a project — carry the
          // user into that project's private chat to continue (no-op when
          // nothing forked). Last, so all local state settled first.
          bindActiveProject(autoProjectId)
        } else if (!(result as { clarify?: boolean }).clarify) {
          // (The clarify outcome already cleared its own spinner and posted the
          // questions — it is a handled stop, not a failure.)
          setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prdGenerating: false, prdCommandThinking: false } : t))
          if (activeTabIdRef.current === tabId) setContent({ prdGenerating: false, prdPartialHtml: null })
          if (deferAck && source.kind === "load" && seedTurn) {
            // An OPEN that found the document busy is a refusal we can explain,
            // not a failed generation — so it settles as prose. The error card
            // the other kinds get would claim the answer didn't come through
            // and that nothing was saved, neither of which happened here.
            settleCommandAck(tabId, seedTurn.id, openFailureReply(result.message))
          } else {
            failDeferredAck(tabId, seedTurn?.id, result.message)
          }
          showToast("PRD unavailable", result.message.slice(0, 200))
        }
      } catch (e) {
        // Both flags off on ANY failure — an indicator that never stops is worse
        // than the dead air it was added to fix.
        setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prdGenerating: false, prdCommandThinking: false } : t))
        if (activeTabIdRef.current === tabId) setContent({ prdGenerating: false, prdPartialHtml: null })
        failDeferredAck(tabId, seedTurn?.id, e instanceof Error ? e.message : String(e))
        showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      }
    })()
    return tabId
  }, [setContent, showToast, hydratePrdThread, openContentPanel, bindConvToPrd, settleCommandAck, failDeferredAck, bindActiveProject])

  // ── Per-tab artifact generation ──────────────────────────────────────────
  const handleOpenPrd = useCallback(async () => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab) return
    // Opening a PRD retires any STANDALONE ticket set on screen — the mirror of
    // `ticketSetOpenScopePatch`, which clears the PRD slots on the way in.
    // `content.ticketSet` is not merely what the Tickets tab renders: it is what
    // makes that tab APPEAR (ContentPanel's hidden gate), so a set left behind
    // by another chat would sit on this PRD's Tickets tab in place of the
    // PRD's own tickets. This is the one path every PRD open goes through.
    setContent({ ticketSet: null, ticketSetGenerating: false, ticketSetStandalone: false })
    // Mid-generation: there is no document to load yet, and kicking another
    // build would duplicate the run — but the rail still has something to show
    // (the live streaming draft / the generating state), and this is the path
    // that refocusing a tab goes through. Returning early here left a tab that
    // said "Generating PRD…" sitting next to a CLOSED panel, with no way back
    // to it until the run finished. Show the panel, start nothing.
    if (tab.prdGenerating) {
      setContent({ prd: null, prdMeta: tab.briefMeta, prdGenerating: true })
      openContentPanel("prd")
      return
    }
    // Already generated (loaded on this tab) — sync to context and open panel.
    if (tab.prd) {
      setContent({ prd: tab.prd, prdMeta: tab.briefMeta })
      openContentPanel("prd")
      return
    }
    // A PRD already exists in the DB for this tab but isn't cached — e.g. after a
    // reload, where `prd` is stripped from the persisted tab. LOAD it by id; do
    // NOT regenerate (that would spawn a duplicate and burn a full generation).
    // This is what makes "View PRD" open the real doc rather than kick off a build.
    //
    // Prefer this tab's OWN saved id: it's stable across brief regeneration and is
    // the ONLY recovery path for backlog PRDs (whose tabs carry no briefMeta). Fall
    // back to the brief insight→PRD map for older tabs that predate `prdId`.
    const savedPrdId = tab.prdId ?? (chatInsightState?.hasPrd ? chatInsightState.prdId : null)
    if (savedPrdId != null) {
      const prdId = savedPrdId
      // LOADING, not generating. Borrowing `prdGenerating` here is what made a
      // reopened chat flash "Generating PRD…" over a document that finished
      // hours ago. The panel still shows its spinner (content.prdGenerating);
      // only the tab's own label distinguishes fetching from writing.
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdLoading: true } : t))
      setContent({ prd: null, prdMeta: null, prdGenerating: true })
      openContentPanel("prd")
      try {
        const result = await loadPrdById(prdId)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdLoading: false, prd: result.prd, prdId: result.prd.prd_id } : t))
          setContent({ prd: result.prd, prdMeta: tab.briefMeta, prdGenerating: false })
        } else if (result.generating) {
          // A healthy IN-FLIGHT PRD, not a failure — e.g. a reload restored this
          // tab mid-generation (the stamped-at-kickoff prdId is how we know it).
          // Re-enter poll+stream instead of toasting: the SSE replay frame
          // repaints everything generated so far, then live deltas continue.
          // It really IS being written — hand the label over from loading to
          // generating, which is the one case where "Generating PRD…" is true.
          setTabs((prev) => prev.map((t) => t.id === activeTabId
            ? { ...t, prdLoading: false, prdGenerating: true } : t))
          const resumed = await resumePrdGeneration(prdId, undefined, (html) => {
            if (activeTabIdRef.current === activeTabId) setContent({ prdPartialHtml: html })
          })
          if (resumed.ok) {
            setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: resumed.prd, prdId: resumed.prd.prd_id } : t))
            if (activeTabIdRef.current === activeTabId) setContent({ prd: resumed.prd, prdMeta: tab.briefMeta, prdGenerating: false, prdPartialHtml: null })
          } else {
            setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
            if (activeTabIdRef.current === activeTabId) setContent({ prdGenerating: false, prdPartialHtml: null })
            showToast("PRD generation failed", resumed.message)
          }
        } else {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdLoading: false, prdGenerating: false } : t))
          setContent({ prdGenerating: false })
          showToast("Couldn't load PRD", result.message)
        }
      } catch (e) {
        // Both flags off on any failure — a label stuck on "Loading PRD…" with
        // a disabled button would leave no way back to the document.
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdLoading: false, prdGenerating: false } : t))
        setContent({ prdGenerating: false })
        showToast("Couldn't load PRD", e instanceof Error ? e.message : "Unknown error")
      }
      return
    }
    // No cached PRD and none saved on this tab yet. An insight-anchored tab
    // (briefMeta) generates that insight's PRD. A PLAIN chat tab (no briefMeta) is
    // seeded from its CONVERSATION — not the brief's default insight, which served
    // an unrelated PRD (the bug). With no conversation to seed from, show the empty
    // prompt instead of an irrelevant document.
    if (!tab.briefMeta) {
      const convTask = conversationToPrdTask(tab.thread)
      if (!convTask) {
        openContentPanel("prd") // nothing to seed from yet — empty state / prompt
        return
      }
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
      setContent({ prd: null, prdMeta: null, prdGenerating: true, prdPartialHtml: null })
      openContentPanel("prd")
      try {
        const { prdApi } = await import("../../../lib/api")
        // Same grounding as the typed command: this button means the same thing
        // ("build a PRD from this chat"), so it must send the same material —
        // it was previously passing the task text alone, not even attachments.
        const start = await prdApi.generateFromTask(convTask, false, prdGroundingDocs(tab.thread))
        // Stamp the id on the tab NOW (it persists to sessionStorage) so a
        // reload mid-generation can resume this run instead of orphaning it.
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdId: start.prd_id } : t))
        // Poll the just-kicked-off task PRD onto THIS tab (keeps the chat + PRD
        // panel together) rather than spawning a separate tab.
        const result = await resumePrdGeneration(start.prd_id, undefined, (html) => setContent({ prdPartialHtml: html }))
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd, prdId: result.prd.prd_id } : t))
          setContent({ prd: result.prd, prdMeta: null, prdGenerating: false, prdPartialHtml: null })
          // The Generate button on a PRD-less tab is explicit generation intent
          // — same summary the typed command gets.
          postSummaryRef.current?.(activeTabId, "prd", result.prd.prd_id)
          // Same main-chat PRD fork continuity as the typed command.
          bindActiveProject(start.project_id ?? null)
        } else {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
          setContent({ prdGenerating: false, prdPartialHtml: null })
          showToast("PRD generation failed", result.message)
        }
      } catch (e) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
        setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("PRD generation failed", e instanceof Error ? e.message : "Unknown error")
      }
      return
    }
    // Insight-anchored tab → generate that brief insight's PRD.
    const meta = tab.briefMeta
    setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
    // Drive the panel's generating spinner via content too (not just per-tab),
    // so the right rail shows in-progress PRD state immediately on open.
    setContent({ prd: null, prdMeta: null, prdGenerating: true, prdPartialHtml: null })
    openContentPanel("prd")
    try {
      const result = await runPrdGeneration(meta, (html) => setContent({ prdPartialHtml: html }))
      if (result.ok) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd, prdId: result.prd.prd_id } : t))
        setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false, prdPartialHtml: null })
        // Reached only when neither the tab nor the insight map knows a PRD
        // (savedPrdId was null) — the CTA read "Generate PRD", so this is a
        // fresh build, not a View reopen.
        postSummaryRef.current?.(activeTabId, "prd", result.prd.prd_id)
      } else {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
        setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("PRD generation failed", result.message)
      }
    } catch (e) {
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
      setContent({ prdGenerating: false, prdPartialHtml: null })
      showToast("PRD generation failed", e instanceof Error ? e.message : "Unknown error")
    }
  }, [activeTabId, chatInsightState, openContentPanel, setContent, showToast, bindActiveProject])

  const handleOpenEvidence = useCallback(async () => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.evidenceGenerating) return
    // Already generated — sync to context and open panel. Stamp the insight meta
    // so the Evidence tab's "Generate/View PRD" bar knows which insight to act on.
    if (tab.evidence) {
      setContent({ evidence: tab.evidence, ...(tab.briefMeta ? { prdMeta: tab.briefMeta } : {}) })
      openContentPanel("evidence")
      return
    }
    const defaultKey = pickDefaultDetailKey(content.briefDetails)
    const meta = tab.briefMeta
      ?? content.detail?.meta
      ?? (defaultKey ? content.briefDetails[defaultKey]?.meta ?? null : null)
    if (!meta) {
      openContentPanel("evidence")
      return
    }
    const tabId = activeTabId
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, evidenceGenerating: true } : t))
    setContent({ evidence: null, evidenceGenerating: true, evidencePartialHtml: null, prdMeta: meta })
    openContentPanel("evidence")
    try {
      const result = await runEvidenceGeneration(meta, undefined, (html) => {
        if (activeTabIdRef.current === tabId) setContent({ evidencePartialHtml: html })
      })
      if (result.ok) {
        setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, evidenceGenerating: false, evidence: result.evidence, evidenceId: result.evidenceId } : t))
        setContent({ evidence: result.evidence, evidenceGenerating: false, evidencePartialHtml: null, prdMeta: meta })
        // Chat summary only when something was BUILT — the read-first path
        // marks a mere reopen of already-ready evidence with `existing`.
        if (!result.existing) postSummaryRef.current?.(tabId, "evidence", result.evidenceId)
      } else {
        setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, evidenceGenerating: false } : t))
        setContent({ evidenceGenerating: false, evidencePartialHtml: null })
        showToast("Evidence generation failed", result.message)
      }
    } catch (e) {
      setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, evidenceGenerating: false } : t))
      setContent({ evidenceGenerating: false, evidencePartialHtml: null })
      showToast("Evidence generation failed", e instanceof Error ? e.message : "Unknown error")
    }
  }, [activeTabId, content.briefDetails, content.detail?.meta, openContentPanel, setContent, showToast])

  // ── Resume orphaned in-flight jobs on (re)mount ───────────────────────────
  // PRD / evidence generation kicks off a fire-and-forget server job; the only
  // client trace is an in-memory *Generating flag + an await closure. A remount
  // (tab backgrounded long enough to unmount, navigate away+back) drops that
  // closure and orphans the running job in the UI though the server finishes.
  // If a pending job id was persisted (jobResume), re-enter the visibility-aware
  // poll against the existing status endpoint — NOT generate again (the resume
  // helpers only GET). Runs once per active tab.
  const resumedTabsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    const meta = tab?.briefMeta
    // Chat-task tabs (no briefMeta) resume through the reload-restore effect →
    // handleOpenPrd, whose DB-load branch re-enters poll+stream when the tab's
    // persisted prdId points at a still-generating PRD.
    if (!meta) return
    if (resumedTabsRef.current.has(activeTabId)) return
    resumedTabsRef.current.add(activeTabId)
    const scope = insightScope(meta.briefId, meta.insightIndex)

    const pendingPrd = getPendingJob("prd", "_", scope)
    if (pendingPrd && !tab?.prd && !tab?.prdGenerating) {
      const prdId = Number(pendingPrd.id)
      if (Number.isFinite(prdId)) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
        setContent({ prd: null, prdMeta: null, prdGenerating: true, prdPartialHtml: null })
        void (async () => {
          try {
            const result = await resumePrdGeneration(prdId, meta, (html) => setContent({ prdPartialHtml: html }))
            if (result.ok) {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd, prdId: result.prd.prd_id } : t))
              setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false, prdPartialHtml: null })
            } else {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
              setContent({ prdGenerating: false, prdPartialHtml: null })
            }
          } catch {
            setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
            setContent({ prdGenerating: false, prdPartialHtml: null })
          }
        })()
      }
    }

    const pendingEvidence = getPendingJob("evidence", "_", scope)
    if (pendingEvidence && !tab?.evidence && !tab?.evidenceGenerating) {
      const evId = Number(pendingEvidence.id)
      if (Number.isFinite(evId)) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: true } : t))
        setContent({ evidencePartialHtml: null })
        void (async () => {
          try {
            const result = await resumeEvidenceGeneration(evId, meta, (html) => {
              if (activeTabIdRef.current === activeTabId) setContent({ evidencePartialHtml: html })
            })
            if (result.ok) {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false, evidence: result.evidence, evidenceId: result.evidenceId } : t))
              setContent({ evidencePartialHtml: null })
              // This path only exists because a FRESH generation was in flight
              // when the screen unmounted (pending-job marker) — summarize it.
              postSummaryRef.current?.(activeTabId, "evidence", result.evidenceId)
            } else {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false } : t))
              setContent({ evidencePartialHtml: null })
            }
          } catch {
            setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false } : t))
            setContent({ evidencePartialHtml: null })
          }
        })()
      }
    }
  }, [activeTabId, setContent])

  const conversations = content.conversations
  const starters = content.ondemandStarters
  const conversationsRef = useRef(conversations)
  conversationsRef.current = conversations

  const profileName =
    auth.kind === "authed" ? profileDisplayName(profile, auth.user.email) : null
  const name =
    content.userName?.split(/\s+/)[0] ??
    profileName?.split(/\s+/)[0] ??
    "there"
  const userInitials = profileName
    ? profileName.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("")
    : name.slice(0, 1).toUpperCase()
  const homeCards = content.homeStarterCards.filter((c) => c.id !== "home-goto-ask")

  // When the active tab changes, sync its cached artifacts into ContentContext so
  // ContentPanel always shows the current tab's PRD / evidence.
  // We do NOT clear content.detail here — it holds the global brief finding context
  // that handleOpenPrd / handleOpenEvidence use as a fallback generation source.
  useEffect(() => {
    const tab = tabsRef.current.find((t) => t.id === activeTabId) ?? null
    setContent({
      prd: tab?.prd ?? null,
      prdMeta: tab?.briefMeta ?? null,
      evidence: tab?.evidence ?? null,
      // When switching tabs, reset the generating flag so the panel reflects
      // this tab's actual state (generating is tracked per-tab in local state).
      evidenceGenerating: tab?.evidenceGenerating ?? false,
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, setContent])

  // Mirror the active tab's CONVERSATION id into content, so the panel knows
  // which thread it's showing (the Reports tab lists that thread's reports).
  // Kept separate from the artifact sync above because it must also fire when a
  // tab GAINS its id — a brand-new chat has none until its first ask persists,
  // and keying on activeTabId alone would leave the panel on a stale thread.
  const activeConvId = tabs.find((t) => t.id === activeTabId)?.dbConvId ?? null
  const prevConvForFocusRef = useRef(activeConvId)
  // The TAB the previous conversation id belonged to. Tracked because the id
  // alone cannot tell a thread SWITCH from a thread coming into EXISTENCE —
  // see the reset effect below, where confusing the two blanked the panel.
  const prevTabForFocusRef = useRef(activeTabId)
  // A loaded thread's project binding, keyed by its DB conversation id —
  // populated by checkResume from the resume payload the instant it's
  // parsed, read by the restore effect just below the reset effect.
  const threadProjectIdByConvIdRef = useRef<Map<number, number | null>>(new Map())
  useEffect(() => {
    // A genuine thread change retires the report POINTER along with the thread.
    // `content.reportFocusId` is written from four places (a report card in the
    // thread, the Artifacts hand-off, the tab strip's reopen button, an Artifacts
    // row) and used to be cleared from exactly one — ReportsTab's "All reports"
    // button, which only exists on a thread with more than one report. So it
    // outlived the thread that set it, and the panel opened the document it named
    // over whatever chat you had moved to.
    //
    // Guarded on an actual change rather than running on mount, because the
    // Artifacts → report hand-off sets the focus a beat AFTER the tab it belongs
    // to gains its conversation id.
    // WHAT COUNTS AS A THREAD CHANGE, stated in one place because getting it
    // wrong is silent in both directions.
    //
    // Keying on the conversation id ALONE treated null → 412 as a switch. On a
    // brand-new chat that transition is not a switch at all: it is THIS tab
    // gaining its identity the moment its conversation row is created. The
    // clear then fired a beat after a document was written from that chat,
    // wiping `documentId` out from under a panel that was already open — the
    // blank Document tab found on staging. The tab stayed visible (we never
    // pull the current tab out from under a reader) over nothing at all.
    //
    // Keying on the TAB alone has the mirror problem: moving between two chats
    // that both have no conversation row yet is a real switch with no id change
    // to notice, and the previous thread's document would ride along.
    //
    // So: a change of TAB is always a switch; a change of ID is a switch unless
    // it is this same tab acquiring its first one.
    const tabChanged = prevTabForFocusRef.current !== activeTabId
    const convChanged = prevConvForFocusRef.current !== activeConvId
    const gainedFirstId =
      !tabChanged && prevConvForFocusRef.current == null && activeConvId != null
    const changed = (tabChanged || convChanged) && !gainedFirstId
    prevConvForFocusRef.current = activeConvId
    prevTabForFocusRef.current = activeTabId
    setContent(changed
      ? {
          conversationId: activeConvId,
          // A project binding belongs to the thread that forked it — the same
          // rule as the report pointer and the document below. Switching to
          // another chat (or starting a new one) must not carry the previous
          // thread's project-menu affordance onto an unbound conversation. The
          // main-chat generate re-binds AFTER its conversation is established,
          // so this clear never races the bind that follows a fork.
          activeProjectId: null,
          reportFocusId: null,
          reportFocusStandalone: false,
          // Cleared with the rest, and for exactly the same reason: a document
          // belongs to the thread that wrote it. Leaving it set meant switching
          // to another chat (or starting a new one) still showed a Document tab
          // rendering the PREVIOUS thread's document.
          documentId: null,
          documentGenerating: false,
        }
      : { conversationId: activeConvId })
    // `activeTabId` is a REAL dependency, not a lint appeasement: without it
    // this effect does not run when the active tab changes between two chats
    // that both still have no conversation row, which is the exact case
    // `tabChanged` exists to catch.
  }, [activeConvId, activeTabId, setContent])

  // Restore the project-menu affordance on a REVISITED thread — the binding
  // itself is a THREAD attribute (unlike the fork bind above, which is a
  // one-shot signal from a just-completed generation and has no restore
  // path — see ChatScreen.active-project-reset.dom.test.tsx). checkResume
  // records a loaded conversation's project id here, keyed by its DB id, the
  // moment the resume payload is parsed; this effect re-applies it every time
  // that conversation becomes active again, ON TOP of the clear above (same
  // activeConvId dependency, declared immediately after it, so effects for
  // the same commit run in this order — the derive is never clobbered by the
  // reset it follows). A conversation with no recorded binding is a no-op:
  // activeProjectId stays whatever the reset above just set it to (null).
  useEffect(() => {
    if (activeConvId == null) return
    const projectId = threadProjectIdByConvIdRef.current.get(activeConvId)
    if (projectId == null) return
    setContent({ activeProjectId: projectId })
  }, [activeConvId, setContent])


  // This thread's captured reports, newest first — fetched once by
  // useThreadReportsSync (AppShell). Defaulted for the surfaces/tests that render
  // ChatScreen against partial content.
  //
  // SCOPED to the conversation the fetch was actually for. That hook runs in
  // AppShell — our PARENT — and React flushes a child's effects before its
  // parent's, so on the commit where the active tab changes, content still holds
  // the OLD thread's rows. Everything downstream (the auto-open below, the tab
  // strip's reopen button, a report card resolving its own document) has to read
  // that as "this thread's list hasn't landed yet", never as this thread's
  // answer — reading it as an answer is what auto-opened a brand-new chat's panel
  // on the previous thread's report.
  const threadReports = useMemo(
    () => (content.threadReportsConversationId === activeConvId
      ? (content.threadReports ?? NO_REPORTS)
      : NO_REPORTS),
    [content.threadReports, content.threadReportsConversationId, activeConvId],
  )

  // Open the report a CHAT TURN is about, from its title.
  //
  // Title is the join key because the reply the thread holds carries no report
  // id: capture runs after the ask completes, deliberately (it must never delay
  // the answer), so the id doesn't exist yet when the reply is stored. Both sides
  // derive the title from the document's own <h1> — the client via
  // reportTitleFromHtml, the server via report_capture.report_title — so they
  // agree by construction.
  //
  // Matched exactly first, then leniently (case/whitespace, then either side
  // being a prefix of the other) — a title that drifts by a dash or a truncation
  // should still open the right document rather than dumping the reader on a
  // list, which is the failure this whole path exists to avoid.
  //
  // No match at all means capture hasn't landed yet (or the row is gone): open
  // the tab and let it show what it has, rather than pointing at a report that
  // isn't there.
  const openReportByTitle = useCallback((title: string) => {
    const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ")
    const want = norm(title)
    const match =
      threadReports.find((r) => r.title === title) ??
      threadReports.find((r) => norm(r.title) === want) ??
      threadReports.find((r) => {
        const have = norm(r.title)
        return have.length > 0 && (have.startsWith(want) || want.startsWith(have))
      })
    if (match) setContent({ reportFocusId: match.id, reportFocusStandalone: false })
    openContentPanel("reports")
  }, [threadReports, setContent, openContentPanel])

  // Chat-task PRDs generate an Evidence artifact server-side (semantic KG
  // retrieval over the task — skipped when the KG has no backing). Once a tab
  // carries a chat-sourced PRD, probe for that doc and land it on the tab: a
  // found row (ready OR still generating) surfaces the Evidence tab (the
  // hidden-gate in ContentPanel clears when evidence/evidenceGenerating is
  // set); a 404 (no backing → skipped) leaves the tab hidden. One probe per
  // (tab, prd) — the ref set is keyed accordingly so a regenerated PRD probes
  // again but re-renders don't.
  const evidenceProbedRef = useRef<Set<string>>(new Set())
  const activePrdId = (() => {
    const tab = tabs.find((t) => t.id === activeTabId)
    return tab?.prd?.source === "chat" ? tab.prd.prd_id : null
  })()
  useEffect(() => {
    if (!activeTabId || activePrdId == null) return
    const tabId = activeTabId
    const probeKey = `${tabId}:${activePrdId}`
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (!tab || tab.evidence || tab.evidenceGenerating) return
    if (evidenceProbedRef.current.has(probeKey)) return
    evidenceProbedRef.current.add(probeKey)
    void (async () => {
      try {
        const { prdApi } = await import("../../../lib/api")
        const rec = await prdApi.evidenceForPrd(activePrdId)
        if (!rec || rec.status === "failed") return
        // A doc exists (ready or in-flight): show the Evidence tab's spinner
        // while resumeEvidenceGeneration polls it to terminal (a ready row
        // resolves on the first poll).
        setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, evidenceGenerating: true } : t))
        if (activeTabIdRef.current === tabId) setContent({ evidenceGenerating: true })
        const result = await resumeEvidenceGeneration(rec.id, undefined)
        setTabs((prev) => prev.map((t) => t.id === tabId
          ? { ...t, evidenceGenerating: false, evidence: result.ok ? result.evidence : null }
          : t))
        if (activeTabIdRef.current === tabId) {
          setContent({ evidenceGenerating: false, ...(result.ok ? { evidence: result.evidence } : {}) })
        }
      } catch {
        // Probe is best-effort — a failed lookup just leaves the tab hidden.
        setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, evidenceGenerating: false } : t))
        if (activeTabIdRef.current === tabId) setContent({ evidenceGenerating: false })
      }
    })()
  }, [activeTabId, activePrdId, setContent])

  useEffect(() => {
    if (!pendingSearchHandoff) return
    const { query, reply, convId } = pendingSearchHandoff
    setPendingSearchHandoff(null)
    const title = query.length > 40 ? `${query.slice(0, 37)}…` : query
    openTab(title, [{ id: convId, query, reply }])
    setActiveConv(0)
  }, [pendingSearchHandoff, setPendingSearchHandoff, openTab])

  useEffect(() => {
    if (pendingOndemandDraft == null || !pendingOndemandDraft.trim()) return
    const text = pendingOndemandDraft
    setPendingOndemandDraft(null)
    // If no active tab, pre-fill the composer; user hits Enter to send
    if (!activeTabId) {
      setDraft(text)
      requestAnimationFrame(() => {
        const ta = composerRef.current
        if (ta) {
          ta.style.height = "auto"
          ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`
          ta.focus()
        }
      })
    } else {
      // Active tab exists — open a new tab with this as the first message
      const title = text.length > 40 ? `${text.slice(0, 37)}…` : text
      openTab(title)
      setDraft(text)
    }
  }, [pendingOndemandDraft, setPendingOndemandDraft, activeTabId, openTab])

  // ── Per-tab Supabase persistence ─────────────────────────────────────────
  // Each tab maps to its OWN conversation, tracked via ChatTab.dbConvId. The
  // persistence helper reads/writes that per-tab id (never a shared ref), so
  // parallel chats record into separate conversations. A single in-flight create
  // per tab keeps the user + assistant turns in ONE conversation under the
  // fire-and-forget timing (see chatPersistence.ts).
  const setTabConvId = useCallback((tabId: string, convId: number) => {
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, dbConvId: convId } : t))
  }, [])
  // Stable single instance — its per-tab in-flight-create map must persist across
  // renders, so we build it once (lazy ref init) rather than per render.
  const persistenceRef = useRef<ReturnType<typeof createChatPersistence> | null>(null)
  if (persistenceRef.current === null) {
    persistenceRef.current = createChatPersistence({
      getApi: () => import("../../../lib/api").then((m) => m.conversationsApi),
      getTabConvId: (tabId) => tabsRef.current.find((t) => t.id === tabId)?.dbConvId ?? null,
      getTabPrdId: (tabId) => tabsRef.current.find((t) => t.id === tabId)?.prdId ?? null,
      setTabConvId: (tabId, convId) => setTabConvId(tabId, convId),
      onConversationCreated: (turnId, convId) => {
        // Tag the in-memory conversation with the DB id so the rail can load turns.
        const latest = conversationsRef.current
        const tagged = latest.map((c) =>
          c.id === turnId ? { ...c, _dbId: convId } as any : c,
        )
        setContent({ conversations: tagged })
      },
    })
  }
  const persistence = persistenceRef.current

  // Back-patch a conversation's prd_id once BOTH the PRD id and the DB
  // conversation id are known. The in-chat command flows (import a document,
  // "generate a PRD for X") create the conversation from their seed turn BEFORE
  // the async import/generate call returns the prd_id — so the conversation is
  // first persisted with prd_id=null. Without this back-patch, reopening that
  // chat from history has no prd_id to rebind the tab to, so the "View PRD"
  // button never renders and the content panel never reopens (the reported bug).
  // Fires at most once per conversation; a failed PATCH is retried on a later
  // render (the id is removed from the seen-set so the next pass re-attempts).
  const patchedPrdConvRef = useRef<Set<number>>(new Set())
  useEffect(() => {
    for (const t of tabs) {
      if (t.prdId == null || t.dbConvId == null) continue
      if (patchedPrdConvRef.current.has(t.dbConvId)) continue
      const convId = t.dbConvId
      const prdId = t.prdId
      patchedPrdConvRef.current.add(convId)
      void import("../../../lib/api")
        .then(({ conversationsApi }) => conversationsApi.update(convId, { prd_id: prdId }))
        .catch(() => { patchedPrdConvRef.current.delete(convId) })
    }
  }, [tabs])

  // Resume a conversation from ChatsScreen or IdeationScreen. Two payload
  // shapes: with `turns` (built locally / legacy) the tab opens pre-filled;
  // with only a `dbId` (All-chats row click) the tab opens INSTANTLY in a
  // `hydrating` state and the turns are fetched here in the background — the
  // click never blocks on the network.
  const checkResume = useCallback(() => {
    try {
      const raw = localStorage.getItem("sprntly_resume_conv")
      if (!raw) return
      localStorage.removeItem("sprntly_resume_conv")
      const data = JSON.parse(raw) as {
        dbId: number
        title: string
        turns?: { role: string; content: string }[]
        /** Preview-derived thread used when the background fetch yields nothing. */
        fallbackTurns?: { role: string; content: string }[]
        /** The PRD this conversation is about (from ConversationRecord.prd_id),
         *  when it was opened from a PRD tab. Re-binds the resumed tab to its PRD
         *  so the "View PRD" button renders and the content panel auto-reopens —
         *  without it, a resumed PRD chat came back as a plain, PRD-less tab. */
        prdId?: number | null
        /** The project this conversation is bound to (from
         *  ConversationRecord.project_id), when one exists. Recorded into
         *  threadProjectIdByConvIdRef so the restore effect can bring back the
         *  project-menu affordance the next time this thread becomes active —
         *  without it, revisiting a project-bound chat came back with no
         *  project-menu at all until another fork rebound it. */
        projectId?: number | null
      }
      if (data.projectId != null) {
        threadProjectIdByConvIdRef.current.set(data.dbId, data.projectId)
      }
      // Re-bind a resumed tab to its PRD: set prdId (only when still null so a
      // reused, live tab is never clobbered) and rehydrate the PRD's saved thread
      // if this tab hasn't got one. Setting prdId is what makes the existing
      // reload-restore effect reopen the panel + render the in-chat PRD button.
      // Also mark the tab `prdInFlow` so the PRD card + clarifying questions render
      // INLINE, right after the command turn (thread[0]) — a resumed PRD chat reads
      // chronologically (question → card → questions). Without it the card +
      // questions were pinned ABOVE the user's own "generate a PRD" message (the
      // reported out-of-order bug). The card node anchors to thread[0] by INDEX, so
      // it survives the background rehydrate that rebuilds turns with fresh ids.
      const bindPrd = (tabId: string, prdId?: number | null) => {
        if (prdId == null) return
        setTabs((prev) => prev.map((t) =>
          t.id === tabId && t.prdId == null ? { ...t, prdId, prdInFlow: true } : t))
        void hydratePrdThread(tabId, prdId)
        // Opening a chat from history is an EXPLICIT request for that chat, so
        // its PRD panel opens every time — not just the first.
        //
        // Neither existing trigger covers a re-open: the reload-restore effect
        // fires at most once per tab (autoRestoredTabsRef), and the switch
        // reconcile needs activeTabId to actually change, which it does not when
        // you re-open the chat you were already on. Between them, the panel
        // appeared the first time and never again. Clearing the claim lets the
        // restore run again for this tab.
        autoRestoredTabsRef.current.delete(tabId)
        setResumePanelTabId(tabId)
      }
      const buildRestored = (
        turns: { role: string; content: string; attachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | null }[],
        keyPrefix: string,
      ): ThreadTurn[] => {
        const restored: ThreadTurn[] = []
        for (let i = 0; i < turns.length; i++) {
          const t = turns[i]
          if (t.role === "user") {
            const next = turns[i + 1]
            const reply = next?.role === "assistant" ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse : undefined
            restored.push({
              id: `${keyPrefix}-${i}`,
              query: t.content,
              reply,
              // Rehydrate persisted attachment texts so the card viewer AND a
              // later "generate a PRD" (conversationPrdDocs) see them after a
              // reload — the second half of the forgotten-document bug.
              ...(t.attachments?.length ? { attachments: t.attachments } : {}),
            })
            if (reply) i++
          } else if (t.role === "assistant" && t.content.trim()) {
            // An assistant row NOT consumed as some user turn's reply — e.g.
            // the artifact summary posted after a generation's ack. These used
            // to be silently dropped, so a persisted summary survived in the
            // DB but vanished from every restored thread. Restore it as an
            // agent-only turn (empty `query` renders no user bubble — the
            // same convention the clarify gate's orphan turn uses live).
            restored.push({
              id: `${keyPrefix}-${i}`,
              query: "",
              reply: { answer: t.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse,
            })
          }
        }
        return restored
      }

      // Pre-fetched turns → open filled (IdeationScreen + no-dbId fallback).
      const preloaded = buildRestored(data.turns ?? [], "resumed")
      if (preloaded.length > 0) {
        // The resumed tab's dbConvId is set via openTab(..., data.dbId) —
        // per-tab now, no shared ref.
        const preTabId = openTab(data.title || "Resumed chat", preloaded, data.dbId)
        setActiveConv(0)
        bindPrd(preTabId, data.prdId)
        return
      }

      // dbId only → open the tab NOW, fetch its history in the background.
      if (!data.dbId) return
      const tabId = openTab(data.title || "Resumed chat", [], data.dbId)
      setActiveConv(0)
      bindPrd(tabId, data.prdId)
      // openTab reuses an existing same-title tab; if it already carries a
      // thread there's nothing to hydrate.
      const existing = tabsRef.current.find((t) => t.id === tabId)
      if (existing && existing.thread.length > 0) return
      setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, hydrating: true } : t)))
      const fallback = buildRestored(data.fallbackTurns ?? [], `resumed-fb-${data.dbId}`)
      void (async () => {
        const { conversationsApi } = await import("../../../lib/api")
        // Fetch the conversation's turns, RETRYING on a transient failure. This
        // is the whole point of the resume: a single failed request must never
        // silently collapse a multi-ask chat down to the preview-only fallback
        // (a lone opening question that looks like a brand-new chat — the exact
        // reported bug). `restored === null` means every attempt errored; an
        // empty array is a genuine empty conversation.
        let restored: ThreadTurn[] | null = null
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            const res = await conversationsApi.listTurns(data.dbId)
            restored = buildRestored(res.turns ?? [], `resumed-${data.dbId}`)
            break
          } catch {
            if (attempt < 2) await new Promise((r) => setTimeout(r, 250 * (attempt + 1)))
          }
        }
        // Prefer the fetched thread whenever it has turns; otherwise the
        // preview-derived fallback keeps the tab usable.
        const finalThread = restored && restored.length > 0 ? restored : fallback
        // Guarded fill: never clobber a thread the user started meanwhile, and
        // never REPLACE a fuller thread (e.g. a reused open tab) with a thinner
        // preview — only fill a still-empty tab.
        setTabs((prev) => prev.map((t) =>
          t.id === tabId
            ? { ...t, hydrating: false, thread: t.thread.length === 0 ? finalThread : t.thread }
            : t))
        // If every attempt failed, say so instead of silently showing a partial
        // thread — silence is what made a temporarily-unreachable history look
        // like the chat had lost its messages.
        if (restored === null) {
          showToast("Couldn't load full chat history", "Showing a preview — reopen the chat to retry.")
        }
      })()
    } catch { /* ignore corrupt data */ }
  }, [openTab, showToast, hydratePrdThread])
  // Check on mount + whenever we navigate to this screen
  useEffect(() => { checkResume() }, [checkResume])
  // Re-check when the route lands on chat (covers goTo("chat") from ChatsScreen)
  useEffect(() => {
    if (currentScreen === "chat") {
      // Small delay to let localStorage write from ChatsScreen settle
      const t = setTimeout(checkResume, 50)
      return () => clearTimeout(t)
    }
  }, [currentScreen, checkResume])

  // The brief is the pinned first tab of this surface. When the route lands on
  // the brief screen (sidebar "Top Insights" → goTo("brief") → /brief, which
  // also renders ChatScreen), activate the pinned brief tab — even if the surface
  // was already mounted on a chat tab.
  useEffect(() => {
    if (currentScreen === "brief") {
      setActiveTabId(BRIEF_TAB_ID)
      setDraft("")
    }
  }, [currentScreen])

  const pushPendingConversation = useCallback(
    (
      turnId: string,
      query: string,
      targetTabId: string,
      turnAttachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[],
    ) => {
      const prev = conversationsRef.current
      const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
      const timeStr = new Date().toISOString()
      // The DB conversation this tab is bound to, if any. A tab resumed from
      // Chat history already carries a `dbConvId` but has NO in-memory rail
      // entry (its conversation lives only in ChatsScreen's DB list). Without
      // this, a follow-up in a resumed room fell through to the `else` branch
      // and prepended a phantom new rail row (titled with the follow-up text)
      // that vanished on reload — the reported bug.
      const dbConvId = tabsRef.current.find((t) => t.id === targetTabId)?.dbConvId ?? null
      // ONE rail entry per chat tab (mirrors the one-conversation-per-tab DB
      // invariant in chatPersistence.ts). A follow-up message in the same room
      // UPDATES that room's entry (latest turn, bumped time, moved to top)
      // instead of prepending a new row — otherwise every message showed up as
      // its own item in the History list until the next page reload. Match by
      // the tab id OR the bound DB conversation id (covers resumed tabs).
      const existing = prev.find((c) =>
        (c as any)._tabId === targetTabId ||
        (dbConvId != null && (c as any)._dbId === dbConvId),
      )
      if (existing) {
        setContent({
          conversations: [
            { ...existing, time: timeStr, savedTurn: { id: turnId, query } },
            ...prev.filter((c) => c !== existing),
          ],
        })
      } else {
        setContent({
          conversations: [
            {
              id: turnId,
              title,
              time: timeStr,
              savedTurn: { id: turnId, query },
              _tabId: targetTabId,
              // Tag with the bound DB conversation id (when resuming an existing
              // thread) so ChatsScreen's `_dbId` dedup folds this into the real
              // DB row instead of rendering it as a separate phantom entry.
              ...(dbConvId != null ? { _dbId: dbConvId } : {}),
            } as ConversationRow,
            ...prev,
          ],
          sidebarConvCount: prev.length + 1,
        })
      }
      // Persist to Supabase against THIS tab's conversation (create-once per tab).
      // Fire-and-forget — failures are swallowed inside the helper.
      void persistence.pushUserTurn(targetTabId, { turnId, title, query, attachments: turnAttachments })
    },
    [setContent, persistence],
  )

  // Returns the persistence promise so a caller that needs the assistant turn
  // to EXIST server-side can wait for it (the next-prompt suggestion fetch does
  // — it reads the thread back out of the DB, and without this it would race
  // the write and see the conversation one turn short). Every other caller
  // ignores the return, exactly as before.
  const finalizeConversationTurn = useCallback(
    (turnId: string, updates: { reply?: AskResponse; error?: string }, targetTabId: string): Promise<void> => {
      const prev = conversationsRef.current
      setContent({
        conversations: prev.map((c) => {
          // Match on the entry's CURRENT saved turn: with one rail entry per
          // tab, later turns land on the same entry, whose id stays the first
          // turn's id. A stale finalize (the entry has since moved on to a
          // newer turn) is dropped rather than clobbering the newer query.
          if (c.savedTurn?.id !== turnId) return c
          const base = { id: turnId, query: c.savedTurn.query }
          if (updates.reply !== undefined) {
            return { ...c, savedTurn: { ...base, reply: updates.reply } }
          }
          if (updates.error !== undefined) {
            return { ...c, savedTurn: { ...base, error: updates.error } }
          }
          return c
        }),
      })
      // Save assistant reply as a turn in this tab's Supabase conversation.
      // The helper awaits any in-flight create so the assistant turn lands in the
      // SAME conversation as its user turn.
      if (updates.reply) {
        return persistence.pushAssistantTurn(targetTabId, replyToText(updates.reply))
      }
      return Promise.resolve()
    },
    [setContent, persistence],
  )
  // Published for openPrdInTab, which is declared above this and settles the
  // clarify-first ack a network round-trip later (see deferredAckRef).
  finalizeTurnRef.current = finalizeConversationTurn

  // Persist a command-seeded turn (the user's command + the agent's ack) to the
  // conversations rail + Supabase, mirroring the pendingPrdTab effect below — the
  // in-chat command flows open the PRD tab DIRECTLY through openPrdInTab (so the
  // render is immediate, not a route-hop away), so they own this persistence that
  // the pendingPrdTab effect does for cross-surface opens. No-op without a seed.
  const seedCommandTurn = useCallback((req: LocalPrdTabRequest, tabId: string) => {
    if (!req.seedQuery) return
    const seedQuery = req.seedQuery
    const turnId = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const title = seedQuery.length > 52 ? `${seedQuery.slice(0, 49)}…` : seedQuery
    // Create this tab's conversation NOW and publish its id, so the in-flight
    // import/generate can hand it to the backend (see seedConvIdRef). This only
    // creates the row — the turns still persist through pushPendingConversation
    // below, which reuses the very same conversation (create-once per tab). The
    // registration is synchronous, so the generate call awaiting it a microtask
    // later always finds it. Doc imports especially need this: their turn
    // persistence waits on a file upload first, which is far too late.
    seedConvIdRef.current.set(
      tabId,
      persistence.ensureConversation(tabId, { turnId, title, query: seedQuery }),
    )
    // "convert this document into a PRD": the doc BECOMES the PRD, so there's no
    // in-chat extracted text (content empty — conversationPrdDocs skips it so it
    // never re-feeds as a source doc). Upload the ORIGINAL file so the chip on the
    // ask can render/download the real document after a reopen; persist with its
    // storage key, and patch the optimistic seed turn so it's viewable live too.
    if (req.source.kind === "importDoc") {
      const file = req.source.file
      void (async () => {
        // Best-effort upload (the `.then` wrapper catches a sync throw too); the
        // seed turn still persists as a name-only chip if storage is unavailable.
        const stored = await Promise.resolve().then(() => attachmentsApi.upload(file)).catch(() => null)
        const attachment = { name: file.name, content: "", key: stored?.key ?? null, mime: stored?.mime ?? null, size: stored?.size ?? null }
        if (stored?.key) {
          setTabs((prev) => prev.map((t) => t.id === tabId
            ? { ...t, thread: t.thread.map((tn) => tn.query === seedQuery ? { ...tn, attachments: [attachment] } : tn) }
            : t))
        }
        pushPendingConversation(turnId, seedQuery, tabId, [attachment])
        finalizeConversationTurn(turnId, { reply: commandAckReply(req) }, tabId)
      })()
      return
    }
    pushPendingConversation(turnId, seedQuery, tabId)
    // "generate a PRD for X": the reply isn't known yet — the clarify gate may
    // answer with QUESTIONS, and persisting "Generating a PRD for that…" here
    // wrote a promise the agent then didn't keep, sitting in the thread (and in
    // the conversation history) above the questions it contradicted. Register
    // the turn instead; settleCommandAck writes whichever reply actually wins.
    //
    // "open the PRD for X" (a `load` with a seed query) defers on the same
    // rule: "Opening that PRD in the panel on the right" is only true once the
    // load succeeds, and a PRD being regenerated refuses to load.
    //
    // `ackInline` is the exception, and MUST be read here rather than
    // re-derived: that open already returned from cache, so registering a turn
    // for it would strand an entry no settle ever consumes — and the next
    // command on this tab would then find that stale entry and persist its own
    // reply against the wrong turn id. See the field's doc comment.
    if (
      req.source.kind === "generateTask" ||
      (req.source.kind === "load" && !req.ackInline)
    ) {
      deferredAckRef.current.set(tabId, { turnId, req })
      return
    }
    finalizeConversationTurn(turnId, { reply: commandAckReply(req) }, tabId)
  }, [pushPendingConversation, finalizeConversationTurn, persistence])

  // "Generate a PRD …" is a COMMAND, not a conversation: it opens the PRD as its
  // OWN chat tab (with the Evidence/PRD/Tickets panel), never as a chat message.
  // Without this the ask agent routes it to the prd-author skill and answers with
  // a raw HTML document dumped into the chat bubble.
  //
  // OPTIMISTIC-FIRST (the latency bug): the previous version awaited
  // prdApi.generateFromTask BEFORE opening the tab, so the composer cleared and
  // the chat sat empty for the multi-second generate call — reading as a frozen
  // app. We now hand the UNSTARTED task to openPrdInTab, which seeds the chat
  // turn + shows the panel spinner on THIS commit and runs generateFromTask
  // inside its own async block (network AFTER the render). The find-or-create
  // backend still serves an already-generated PRD from the DB rather than
  // regenerating it.
  // The user answered the clarify questions (or said "generate now") in a tab
  // whose task was parked by the sufficiency gate: run generation IN THIS TAB
  // with the combined task. Mirrors the generateTask panel block, minus the
  // clarify gate (one round of questions, then we generate — re-gating would
  // loop a terse but sufficient answer back into more questions).
  const runClarifiedGeneration = useCallback((
    prdApi: Pick<typeof import("../../../lib/api").prdApi, "generateFromTask">,
    targetTabId: string,
    rawTask: string,
    sourceDocs: { name: string; content: string }[] | undefined,
    userMessage: string,
  ) => {
    // The combined task (original command + the user's clarify answers, which
    // echo each question's prompt for legibility) can outgrow the backend's
    // 4000-char `task` cap — a 422 AFTER the "Generating a PRD…" ack is on
    // screen. Trim the tail to fit: losing the last answer's tail beats losing
    // the whole generation.
    const TASK_MAX = 4000
    const task = rawTask.length > TASK_MAX ? `${rawTask.slice(0, TASK_MAX - 1)}…` : rawTask
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const ack: AskResponse = {
      answer:
        // This ack lands on a LATER turn of an existing command tab, so the PRD
        // card sits further up the thread (it anchors to thread[0]) — neither
        // "above" nor "below" is reliably true here, so point at the chat.
        "Generating a PRD for that — it'll open in the panel on the right when ready. Use the View PRD button in this chat to reopen the panel anytime.",
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    setTabs((prev) => prev.map((t) => t.id === targetTabId
      ? {
          ...t,
          pendingClarify: undefined,
          prdGenerating: true,
          thread: [...t.thread, { id, query: userMessage, reply: ack }],
        }
      : t))
    if (targetTabId === activeTabIdRef.current) {
      setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
      // The rail was deliberately NOT opened while the questions were pending
      // (see `clarifyFirst` in openPrdInTab), so answering them is what opens
      // it — otherwise the generation would run with no panel to land in.
      setPrdPanelPending("prd")
    }
    pushPendingConversation(id, userMessage, targetTabId)
    finalizeConversationTurn(id, { reply: ack }, targetTabId)
    void (async () => {
      const onPartial = (html: string) => {
        if (activeTabIdRef.current === targetTabId) setContent({ prdPartialHtml: html })
      }
      try {
        // Same durable binding as the command flows, and free here: the tab has
        // been chatting (the clarifying questions landed in it), so its
        // conversation already exists and the id is a synchronous read — no
        // round-trip in front of the user's generation.
        const knownConvId = tabsRef.current.find((t) => t.id === targetTabId)?.dbConvId ?? null
        const start = await prdApi.generateFromTask(task, false, sourceDocs, knownConvId)
        if (knownConvId == null) void bindConvToPrd(targetTabId, start.prd_id)
        setTabs((prev) => prev.map((t) => t.id === targetTabId
          ? { ...t, prdId: start.prd_id, title: start.title ? `PRD · ${start.title}` : t.title }
          : t))
        const result = await resumePrdGeneration(start.prd_id, undefined, onPartial)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === targetTabId
            ? { ...t, prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false }
            : t))
          if (activeTabIdRef.current === targetTabId) {
            setContent({ prd: result.prd, prdGenerating: false, prdPartialHtml: null })
          }
          // Always a fresh generation here (the clarify gate only parks NEW
          // tasks) — post the chat summary of what got built.
          postSummaryRef.current?.(targetTabId, "prd", result.prd.prd_id)
          // Same main-chat PRD fork continuity as the typed/Generate-button paths.
          bindActiveProject(start.project_id ?? null)
        } else {
          setTabs((prev) => prev.map((t) => t.id === targetTabId ? { ...t, prdGenerating: false } : t))
          if (activeTabIdRef.current === targetTabId) setContent({ prdGenerating: false, prdPartialHtml: null })
          showToast("PRD unavailable", result.message.slice(0, 200))
        }
      } catch (e) {
        setTabs((prev) => prev.map((t) => t.id === targetTabId ? { ...t, prdGenerating: false } : t))
        if (activeTabIdRef.current === targetTabId) setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      }
    })()
  }, [finalizeConversationTurn, pushPendingConversation, setContent, showToast, bindConvToPrd, bindActiveProject])

  /** Freeze the questions turn into its settled, read-only form. Both answering
   *  paths call this — the card's submit and a prose reply in the composer — so
   *  the batch never reverts to the flattened text the moment it's answered. */
  // The dock slot lower-priority question batches (the PRD's "User input
  // needed" items) portal their stepper into. State, not a ref, because the
  // portal must re-render when the element mounts.
  const [questionDockEl, setQuestionDockEl] = useState<HTMLDivElement | null>(null)

  // The clarify POPUP's per-batch dismissal, keyed by the questions turn id.
  // Dismissing (its ×) is "answer somewhere else, not in a stepper": the
  // inline card comes back as the fallback answering surface, exactly the UI
  // this popup replaced — so closing the popup can never strand the questions.
  const [clarifyPopupDismissed, setClarifyPopupDismissed] = useState<Record<string, boolean>>({})

  const markClarifyResolved = useCallback(
    (tabId: string, turnId: string, resolution: ClarifyResolution) => {
      setTabs((prev) => prev.map((t) => t.id === tabId
        ? {
            ...t,
            thread: t.thread.map((tn) =>
              tn.id === turnId ? { ...tn, clarifyResolved: resolution } : tn),
          }
        : t))
    },
    [],
  )

  // The clarify CARD's submit — the same landing point as answering in the
  // composer (submitAsk's pendingClarify intercept), reached from the buttons
  // instead of prose. An empty answer set is a "generate now": the original task,
  // every question left to its stated default. Both paths converge on
  // runClarifiedGeneration, so the card is an affordance over the existing flow
  // rather than a second one to keep in sync.
  // ── Artifact chat summaries ────────────────────────────────────────────────
  // When a PRD / evidence report / prototype finishes generating in the panel,
  // the chat posts a short LLM summary of what got built — the thread's record
  // of the outcome, instead of going quiet after the acknowledgment. Posted as
  // an agent-only turn (empty query) and persisted via pushAssistantTurn, so it
  // survives reload AND reopening from history (the restore paths rebuild
  // unconsumed assistant rows as agent-only turns — see buildRestored).
  //
  // Fresh generations only: every caller sits on a path that just RAN a
  // generation, never on a reopen/load path, and the per-artifact guard below
  // makes even a double-fired completion idempotent. Best-effort throughout —
  // a failed summary changes nothing about the artifact flow.
  const postedSummariesRef = useRef<Set<string>>(new Set())
  const postArtifactSummary = useCallback(
    (tabId: string, kind: "prd" | "evidence" | "prototype" | "ticket_set", artifactId: number) => {
      const key = `${tabId}:${kind}:${artifactId}`
      if (postedSummariesRef.current.has(key)) return
      postedSummariesRef.current.add(key)
      // The pointer line doubles as the transcript-filter marker (see
      // *_SUMMARY_ANSWER_RE / PRD_ACK_ANSWER_RE): it is what keeps the summary
      // out of the next PRD's grounding.
      const pointer =
        kind === "prd"
          ? "Use the View PRD button in this chat to reopen it anytime."
          : kind === "evidence"
            ? "Use the View Evidence button in this chat to reopen it anytime."
            : kind === "ticket_set"
              // "them", not "it" — a set is a roster of tickets. Word-for-word
              // the ack's pointer, so TICKET_SET_ANSWER_RE covers both turns.
              ? "Use the View Tickets button in this chat to reopen them anytime."
              : "Use the View Prototype button in this chat to reopen it anytime."
      // The turn appears NOW, in a "Summarizing…" state — the summary call is
      // its own model round-trip, and an answer materializing out of nowhere
      // seconds after the panel settled read as unrelated. Resolved → the same
      // turn takes the reply (typing reveal included); empty/failed → the turn
      // is removed outright, never left as a skeleton nothing will fill.
      const turnId = `summary-${kind}-${artifactId}-${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : Date.now()}`
      setTabs((prev) => prev.map((t) =>
        t.id === tabId
          ? { ...t, thread: [...t.thread, { id: turnId, query: "", summaryPending: true }] }
          : t))
      const dropPendingTurn = () =>
        setTabs((prev) => prev.map((t) =>
          t.id === tabId ? { ...t, thread: t.thread.filter((tn) => tn.id !== turnId) } : t))
      void (async () => {
        try {
          // Static `artifactsApi` binding, deliberately NOT `await import(...)`:
          // this file already imports lib/api statically, so the dynamic form
          // split nothing — and under vitest it raced the mock registry, with
          // this late detached import resolving the REAL module while every
          // earlier import in the same flow got the test's mock.
          const { summary } = await artifactsApi.chatSummary(kind, artifactId)
          if (!summary?.trim()) {
            dropPendingTurn()
            return
          }
          const text = `${summary.trim()}\n\n${pointer}`
          setTabs((prev) => prev.map((t) =>
            t.id === tabId
              ? {
                  ...t,
                  thread: t.thread.map((tn) =>
                    tn.id === turnId
                      ? {
                          ...tn,
                          summaryPending: undefined,
                          reply: {
                            answer: text,
                            sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
                          } as AskResponse,
                        }
                      : tn),
                }
              : t))
          // Hold the WRITE (not the render) until any ask in flight on this
          // tab has landed its own reply. chatPersistence's per-tab queue
          // preserves ENQUEUE order, so persisting mid-ask would slot the
          // summary between a user turn and its answer — the history restore
          // pairs strictly user→next-assistant, so it would show the summary
          // as the answer to that question and orphan the real reply. Capped
          // so a wedged ask can never strand the summary entirely.
          for (let waited = 0; askingTabsRef.current.has(tabId) && waited < 60_000; waited += 250) {
            await new Promise((r) => setTimeout(r, 250))
          }
          // Persist directly — an agent-only message has no rail turn to
          // finalize. No-ops harmlessly on tabs without a conversation (those
          // never appear in Chat history, so nothing the user could reopen is
          // missing it).
          void persistence.pushAssistantTurn(tabId, text)
        } catch {
          // Best-effort: the artifact flow already succeeded — just retire the
          // indicator.
          dropPendingTurn()
        }
      })()
    },
    [persistence],
  )
  postSummaryRef.current = postArtifactSummary

  // A chat-kicked prototype build settled. Resolve the owning tab AT SETTLE
  // TIME from the prototype's own prd_id — the build outlives renders (and the
  // user may have switched tabs mid-build), so any render-time closure could
  // name the wrong tab. Failure results post nothing: the overlay/toast already
  // reports those, and a summary of a failed build would be noise.
  const handlePrototypeSettled = useCallback(
    (result?: import("../../../lib/runDesignAgentGeneration").DesignAgentGenResult) => {
      if (!result?.ok) return
      const prdId = result.prototype.prd_id
      const tab = prdId != null
        ? tabsRef.current.find((t) => t.prdId === prdId)
        : tabsRef.current.find((t) => t.id === activeTabIdRef.current)
      if (!tab) return
      postArtifactSummary(tab.id, "prototype", result.prototype.id)
    },
    [postArtifactSummary],
  )

  const submitClarifyAnswers = useCallback(async (answers: ClarifyAnswer[]) => {
    const tabId = activeTabIdRef.current
    if (!tabId) return
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (!tab?.pendingClarify) return
    const { task, sourceDocs, turnId } = tab.pendingClarify
    const detail = clarifyAnswersText(answers)
    const combined = detail ? `${task}\n\nAdditional details from the user:\n${detail}` : task
    // Stamp the outcome onto the questions turn BEFORE generation starts, so the
    // card flips straight from input to record with no frame of flattened text
    // in between. `clarifyResolved` (not `pendingClarify`) is what holds the
    // structure open from here on.
    markClarifyResolved(tabId, turnId, {
      answers,
      mode: answers.length ? "card" : "skip",
    })
    const { prdApi } = await import("../../../lib/api")
    runClarifiedGeneration(prdApi, tabId, combined, sourceDocs, detail || "Generate now")
  }, [runClarifiedGeneration, markClarifyResolved])

  // An edit-phrased message on a PRD tab ("make this PRD shorter", "add a
  // rollout section to the PRD") routes to the scoped chat-edit endpoint: the
  // document actually changes (issue b — before this, the ask agent answered in
  // text and the PRD stayed untouched). Renders the user turn + thinking
  // skeleton optimistically, then confirms which sections changed and refreshes
  // the panel with the server's updated document — the same refresh contract
  // the input-question answer flow uses.
  const prdChatEditFlow = useCallback(async (instruction: string, targetTabId: string, prdId: number) => {
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    setTabs((prev) => prev.map((t) =>
      t.id === targetTabId ? { ...t, thread: [...t.thread, { id, query: instruction }] } : t))
    setBusyTabs((prev) => addToSet(prev, targetTabId))
    pushPendingConversation(id, instruction, targetTabId)
    const finalize = (reply: AskResponse) => {
      setTabs((prev) => prev.map((t) =>
        t.id === targetTabId
          ? { ...t, thread: t.thread.map((tn) => (tn.id === id ? { ...tn, reply } : tn)) }
          : t))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }
    try {
      const { prdApi } = await import("../../../lib/api")
      const res = await prdApi.chatEdit(prdId, instruction)
      if (res.sections_changed.length) {
        // The scoped edit produced a fresh document — drop stale local drafts so
        // the panel shows the server copy, then push it into the tab + panel.
        clearPrdDrafts(prdId)
        const prd = prdStateFromRecord(res.prd)
        setTabs((prev) => prev.map((t) => (t.id === targetTabId ? { ...t, prd } : t)))
        if (targetTabId === activeTabIdRef.current) setContent({ prd })
      }
      const answer = res.sections_changed.length
        ? `Updated ${res.sections_changed.join(", ")}${res.summary ? ` — ${res.summary}` : "."}`
        : res.summary ||
          "That didn't read as a change to the document, so I left the PRD as is — tell me what to update and I'll apply it."
      finalize({ answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse)
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      finalize({
        answer: `I couldn't update the PRD — ${msg}. The document is unchanged; try rephrasing the edit.`,
        sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse)
    } finally {
      setBusyTabs((prev) => removeFromSet(prev, targetTabId))
    }
  }, [finalizeConversationTurn, pushPendingConversation, setContent])

  // "Change the template to Acme" on a PRD tab: dispatch the in-place format
  // switch (POST /v1/prd/{id}/change-template) and acknowledge in the thread —
  // the ack posts on dispatch, like the ticket-set ack, because the re-write
  // renders live in the panel and the thread's job is to say what started and
  // where to look. The regeneration's OUTCOME lands as a toast (the same pair
  // the panel's own Format control shows), read from the row's stamp: a failed
  // regeneration is restored to `ready` with its content intact and its OLD
  // stamp — unchanged stamp, unchanged document.
  const prdChangeTemplateFlow = useCallback(async (
    query: string, targetTabId: string, prdId: number,
    templateId: string, templateName: string | null,
  ) => {
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    setTabs((prev) => prev.map((t) =>
      t.id === targetTabId ? { ...t, thread: [...t.thread, { id, query }] } : t))
    setBusyTabs((prev) => addToSet(prev, targetTabId))
    pushPendingConversation(id, query, targetTabId)
    const finalize = (reply: AskResponse) => {
      setTabs((prev) => prev.map((t) =>
        t.id === targetTabId
          ? { ...t, thread: t.thread.map((tn) => (tn.id === id ? { ...tn, reply } : tn)) }
          : t))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }
    const label = templateName ? `“${templateName}”` : "that format"
    let res: { status: "ready" | "generating"; unchanged?: boolean; artifact_template_id: string | null }
    try {
      const { prdApi } = await import("../../../lib/api")
      res = await prdApi.changeTemplate(prdId, templateId)
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      finalize({
        answer: `I couldn't switch the format — ${msg}. The PRD is unchanged, and its version history is intact.`,
        sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse)
      setBusyTabs((prev) => removeFromSet(prev, targetTabId))
      return
    }
    if (res.unchanged) {
      finalize({
        answer: `This PRD is already written in ${label} — nothing to change.`,
        sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse)
      setBusyTabs((prev) => removeFromSet(prev, targetTabId))
      return
    }
    finalize({
      answer: `Switching this PRD to ${label} — re-writing it into that structure now. It'll re-render in the panel on the right, and the previous version is saved in Version history.`,
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse)
    // The turn is answered; the thread stays usable while the re-write runs.
    setBusyTabs((prev) => removeFromSet(prev, targetTabId))

    // Drive the panel exactly like a first generation: stale drafts cleared (a
    // local draft must not overwrite the re-laid-out document), the tab and
    // panel flip to generating, and the poll + SSE stream render it live.
    clearPrdDrafts(prdId)
    setTabs((prev) => prev.map((t) =>
      t.id === targetTabId ? { ...t, prd: null, prdGenerating: true } : t))
    if (targetTabId === activeTabIdRef.current) {
      setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
      openContentPanel("prd")
    }
    try {
      const { resumePrdGeneration, loadPrdById } = await import("../../../lib/runPrdGeneration")
      const result = await resumePrdGeneration(prdId, undefined, (html) => {
        if (targetTabId === activeTabIdRef.current) setContent({ prdPartialHtml: html })
      })
      const prd = result.ok
        ? result.prd
        // Timeout/hiccup: the backend preserved the document — reload it so the
        // panel shows the honest state, never a blank pane.
        : await loadPrdById(prdId).then((r) => (r.ok ? r.prd : null)).catch(() => null)
      setTabs((prev) => prev.map((t) =>
        t.id === targetTabId ? { ...t, prd, prdGenerating: false } : t))
      if (targetTabId === activeTabIdRef.current) {
        setContent({ prd, prdGenerating: false, prdPartialHtml: null })
      }
      if (result.ok && (result.prd.artifactTemplateId ?? null) === templateId) {
        showToast("Format switched", `This PRD is now written in ${templateName || "the new format"}.`)
      } else {
        showToast("Couldn't switch the format", "The PRD is unchanged — its content and version history are intact. Try again in a moment.")
      }
    } catch {
      showToast("Couldn't switch the format", "The PRD is unchanged — its content and version history are intact. Try again in a moment.")
    }
  }, [finalizeConversationTurn, pushPendingConversation, setContent, openContentPanel, showToast])

  // ── Change the TICKETS' format from chat ────────────────────────────────────
  // "Change the ticket template to Acme". The tickets counterpart of
  // prdChangeTemplateFlow, but synchronous end to end: the backend re-LAYS the
  // existing tickets (identity, edits and tracker links preserved — never a
  // regeneration) and answers with the re-laid set, so there is no generating
  // state to drive and no poll. `target` is the thread's standalone set when it
  // has one, else the tab PRD's persisted tickets — resolved by the caller,
  // because the backend cannot see a set from a prd_id-shaped envelope.
  const ticketsChangeTemplateFlow = useCallback(async (
    query: string, targetTabId: string,
    target: { ticketSetId: number } | { prdId: number },
    templateId: string, templateName: string | null,
  ) => {
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    setTabs((prev) => prev.map((t) =>
      t.id === targetTabId ? { ...t, thread: [...t.thread, { id, query }] } : t))
    setBusyTabs((prev) => addToSet(prev, targetTabId))
    pushPendingConversation(id, query, targetTabId)
    const finalize = (reply: AskResponse) => {
      setTabs((prev) => prev.map((t) =>
        t.id === targetTabId
          ? { ...t, thread: t.thread.map((tn) => (tn.id === id ? { ...tn, reply } : tn)) }
          : t))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }
    const asReply = (answer: string) => ({
      answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse)
    const label = templateName ? `“${templateName}”` : "that format"
    try {
      const { storiesApi } = await import("../../../lib/api")
      const res = await storiesApi.changeTemplate(target, templateId)
      if (res.unchanged) {
        finalize(asReply(`These tickets are already written in ${label} — nothing to change.`))
        return
      }
      finalize(asReply(
        `Done — the tickets now use ${label}. Every ticket kept its content, edits and tracker links; only the description layout changed. They're in the panel on the right.`,
      ))
      // Re-render the panel from the persisted truth. A standalone set is
      // re-read through its one owner (loadTicketSet republishes the slice);
      // a PRD's tickets re-read via the tab's cache-first effect on the nonce.
      if (targetTabId === activeTabIdRef.current) {
        if ("ticketSetId" in target) {
          void loadTicketSet(target.ticketSetId, setContent)
        } else {
          setContent({ ticketsRefreshNonce: Date.now() })
        }
        openContentPanel("tickets")
      }
      showToast("Format switched", `These tickets now use ${templateName || "the new format"}.`)
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      finalize(asReply(`I couldn't switch the ticket format — ${msg}. The tickets are unchanged.`))
    } finally {
      setBusyTabs((prev) => removeFromSet(prev, targetTabId))
    }
  }, [finalizeConversationTurn, pushPendingConversation, setContent, openContentPanel, showToast])

  // ── Assign tickets from chat ────────────────────────────────────────────────
  // "Assign the auth ticket to Dave" / "give these tickets to Priya and Sam".
  // POST /v1/tickets/assign-plan resolves the sentence against the thread PRD's
  // tickets and the team roster. Pairs the request stated OUTRIGHT are applied
  // here immediately — through the same PUT /fields the drawer's picker makes,
  // so chat gains no write path of its own. Everything the request left open
  // comes back as questions and steps through the dock's QuestionPopup; the
  // picks stay local until the last question settles, then `completeAssign`
  // writes them all and posts the summary (owner directive: finish all the
  // questions before anything is sent).
  const assignTicketsFlow = useCallback(async (
    query: string, targetTabId: string, prdId: number, instruction: string,
  ) => {
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    setTabs((prev) => prev.map((t) =>
      t.id === targetTabId ? { ...t, thread: [...t.thread, { id, query }] } : t))
    setBusyTabs((prev) => addToSet(prev, targetTabId))
    pushPendingConversation(id, query, targetTabId)
    const finalize = (reply: AskResponse) => {
      setTabs((prev) => prev.map((t) =>
        t.id === targetTabId
          ? { ...t, thread: t.thread.map((tn) => (tn.id === id ? { ...tn, reply } : tn)) }
          : t))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }
    const asReply = (answer: string) => ({
      answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse)
    try {
      const plan = await ticketDataApi.assignPlan(prdId, instruction)
      // Sequential on purpose: a handful of writes at most, and a per-ticket
      // failure must be attributable to its ticket rather than lost in a race.
      const applied: string[] = []
      const failed: string[] = []
      for (const a of plan.assignments) {
        try {
          await ticketDataApi.saveFields(a.ticket_key, { assignee: a.assignee })
          applied.push(`“${a.ticket_title}” → ${a.assignee.display_name || a.assignee.email || "them"}`)
        } catch {
          failed.push(a.ticket_title)
        }
      }
      const noteLine = [
        plan.note,
        failed.length
          ? `I couldn't save ${failed.map((t) => `“${t}”`).join(", ")} — try those from the ticket itself.`
          : "",
      ].filter(Boolean).join(" ")
      if (plan.questions.length) {
        setTabs((prev) => prev.map((t) => t.id === targetTabId
          ? { ...t, pendingAssign: { questions: plan.questions, applied, turnId: id } }
          : t))
        const lead = applied.length
          ? `Done so far:\n${applied.map((l) => `- ${l}`).join("\n")}\n\n`
          : ""
        const qWord = plan.questions.length === 1 ? "one more answer" : `${plan.questions.length} quick answers`
        finalize(asReply(
          `${noteLine ? `${noteLine}\n\n` : ""}${lead}I need ${qWord} to finish — pick below; I'll apply everything once you've been through them.`,
        ))
      } else if (applied.length || noteLine) {
        finalize(asReply(
          `${applied.length ? `Assigned:\n${applied.map((l) => `- ${l}`).join("\n")}` : ""}${applied.length && noteLine ? "\n\n" : ""}${noteLine}`,
        ))
      } else {
        finalize(asReply(
          "I couldn't work out that assignment — try naming the ticket and the person, e.g. “assign the login ticket to Dave”.",
        ))
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      finalize(asReply(`I couldn't plan that assignment — ${msg}. No tickets were changed.`))
    } finally {
      setBusyTabs((prev) => removeFromSet(prev, targetTabId))
    }
  }, [finalizeConversationTurn, pushPendingConversation])

  // ── share_to_slack ────────────────────────────────────────────────────────
  //
  // "Share this PRD on my slack channel and ask the team for feedback."
  //
  // Two steps, always, and the split is the feature: this flow only ever
  // PREVIEWS. It resolves the document and the channel, seeds a turn carrying
  // the preview card, and stops. The post happens in `sendSlackShare` below,
  // when the person has looked at the message and pressed the button — because
  // a message in a team channel is public and cannot be recalled.

  /** Patch one turn's `slackShare` state in place. Used by every step after
   *  the seed (picking a different document, sending, cancelling, failing), so
   *  the card and the record it becomes live on the same persisted turn. */
  const patchSlackShare = useCallback((
    tabId: string,
    turnId: string,
    patch: Partial<NonNullable<ThreadTurn["slackShare"]>>,
  ) => {
    setTabs((prev) => prev.map((t) => t.id === tabId
      ? {
          ...t,
          thread: t.thread.map((tn) => tn.id === turnId && tn.slackShare
            ? { ...tn, slackShare: { ...tn.slackShare, ...patch } }
            : tn),
        }
      : t))
  }, [])

  /** The reference a share posts back — the client's OWN context first.
   *
   *  "Share this PRD" means the document in front of the user, so an explicit
   *  id from the tab beats the planner's reading of the phrase every time; the
   *  phrase is the fallback for "share the checkout PRD", where there is no
   *  tab context to prefer. The backend applies the same precedence, so the
   *  two cannot disagree about which document was meant. */
  const shareRefFor = useCallback((
    envelope: ChatIntentEnvelope,
    tab: ChatTab | undefined,
    /** The report the panel is currently showing (`content.reportFocusId`) —
     *  passed in rather than read here so this stays a pure mapping of
     *  context → reference, testable without the content store. */
    reportId: number | null,
  ): SlackShareTargetRef => {
    const prdId = envelope.prd_id ?? tab?.prdId ?? null
    // A KIND the user named wins over the tab's default document: "share the
    // tickets" in a thread that has both a PRD and a set means the set, and
    // falling through to prd_id there would share the wrong artifact under a
    // name the user did give us.
    const named = (envelope.artifact_type || "").toLowerCase()
    if (named === "tickets" && tab?.ticketSetId) {
      return { ticket_set_id: tab.ticketSetId }
    }
    if (named === "report" && reportId) {
      return { report_id: reportId }
    }
    if (named === "prd" && prdId) {
      return { prd_id: prdId }
    }
    // No subject named ("share this on slack") → whatever is in front of them,
    // in the order the panel stacks it.
    if (!envelope.artifact_query) {
      if (prdId) return { prd_id: prdId }
      if (tab?.ticketSetId) return { ticket_set_id: tab.ticketSetId }
      if (reportId) return { report_id: reportId }
    }
    // A named subject with no matching tab context — "share the checkout PRD"
    // — resolves by title against the caller's own library, server-side.
    return {
      artifact_type: envelope.artifact_type ?? null,
      artifact_query: envelope.artifact_query ?? null,
    }
  }, [])

  const shareToSlackFlow = useCallback(async (
    query: string, targetTabId: string, envelope: ChatIntentEnvelope,
  ) => {
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    setTabs((prev) => prev.map((t) =>
      t.id === targetTabId ? { ...t, thread: [...t.thread, { id, query }] } : t))
    setBusyTabs((prev) => addToSet(prev, targetTabId))
    pushPendingConversation(id, query, targetTabId)
    const asReply = (answer: string) => ({
      answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse)
    const finalize = (reply: AskResponse, share?: ThreadTurn["slackShare"]) => {
      setTabs((prev) => prev.map((t) =>
        t.id === targetTabId
          ? {
              ...t,
              thread: t.thread.map((tn) => tn.id === id
                ? { ...tn, reply, ...(share ? { slackShare: share } : {}) }
                : tn),
            }
          : t))
      finalizeConversationTurn(id, { reply }, targetTabId)
    }

    const tab = tabsRef.current.find((t) => t.id === targetTabId)
    const ref = shareRefFor(envelope, tab, content.reportFocusId ?? null)
    try {
      const preview = await slackShareApi.preview(ref, {
        channel: envelope.share_channel ?? null,
        note: envelope.share_note ?? null,
      })
      // The prose is deliberately short and NEVER claims a post happened — the
      // card below it is the whole interaction, and a reply that got ahead of
      // it is exactly the failure this two-step flow exists to prevent.
      const lead =
        preview.status === "ready"
          ? "Here's what I'll post — have a look before I send it."
          : preview.status === "needs_channel"
            ? "Almost — I just need to know where this should go."
            : preview.status === "blocked"
              ? "I can't post there yet."
              : preview.status === "unsupported_type"
                ? "That one can't be shared to Slack."
                : "Which document did you mean?"
      finalize(asReply(lead), { ref, preview })
      const question = slackShareQuestionFor(preview)
      if (question) {
        setTabs((prev) => prev.map((t) => t.id === targetTabId
          ? { ...t, pendingShare: { turnId: id, ...question } }
          : t))
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      // Says plainly that nothing went out. An error here is most often "Slack
      // is not connected", and the user must not be left wondering whether a
      // half-failed share reached the channel anyway.
      finalize(asReply(
        `I couldn't set that share up — ${msg}. Nothing was posted to Slack.`,
      ))
    } finally {
      setBusyTabs((prev) => removeFromSet(prev, targetTabId))
    }
  }, [finalizeConversationTurn, pushPendingConversation, shareRefFor, content.reportFocusId])

  const sendSlackShare = useCallback(async (
    tabId: string, turnId: string, channelId: string, note: string,
  ) => {
    const tab = tabsRef.current.find((t) => t.id === tabId)
    const share = tab?.thread.find((tn) => tn.id === turnId)?.slackShare
    if (!share || share.resolved || share.busy) return
    const channelName =
      share.preview.channel?.name
      ?? (share.preview.channels ?? []).find((c) => c.id === channelId)?.name
      ?? "the channel"
    patchSlackShare(tabId, turnId, { busy: true })
    try {
      await slackShareApi.send(share.ref, channelId, note)
      patchSlackShare(tabId, turnId, {
        busy: false,
        resolved: { outcome: "sent", channelName },
      })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Slack rejected the message"
      patchSlackShare(tabId, turnId, {
        busy: false,
        resolved: { outcome: "failed", error: msg },
      })
    }
  }, [patchSlackShare])

  /** The user picked which document from an ambiguous match — re-preview on
   *  that one, keeping the channel and note they already had. */
  const repreviewSlackShare = useCallback(async (
    tabId: string, turnId: string, target: SlackShareTarget,
  ) => {
    const tab = tabsRef.current.find((t) => t.id === tabId)
    const share = tab?.thread.find((tn) => tn.id === turnId)?.slackShare
    if (!share || share.resolved) return
    const ref: SlackShareTargetRef =
      target.type === "prd" ? { prd_id: target.id }
      : target.type === "report" ? { report_id: target.id }
      : target.type === "ticket_set" ? { ticket_set_id: target.id }
      : { custom_artifact_id: target.id }
    patchSlackShare(tabId, turnId, { busy: true })
    try {
      const preview = await slackShareApi.preview(ref, {
        channel: share.preview.channel?.name ?? share.preview.channel_query ?? null,
      })
      patchSlackShare(tabId, turnId, { busy: false, ref, preview })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      patchSlackShare(tabId, turnId, {
        busy: false,
        resolved: { outcome: "failed", error: msg },
      })
    }
  }, [patchSlackShare])

  /** The share question settled — re-preview on the answer.
   *
   *  A re-preview rather than a local patch, and for both kinds: the server is
   *  what knows whether the chosen channel is one Sprntly can post to, and
   *  patching `status: "ready"` client-side would offer a Send for a private
   *  channel the bot cannot join. One round trip buys the same guarantees the
   *  first preview gave. */
  const completeShareQuestion = useCallback(async (
    tabId: string, answers: PopupAnswer[],
  ) => {
    const tab = tabsRef.current.find((t) => t.id === tabId)
    const ps = tab?.pendingShare
    if (!ps) return
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, pendingShare: undefined } : t))
    const share = tabsRef.current
      .find((t) => t.id === tabId)?.thread.find((tn) => tn.id === ps.turnId)?.slackShare
    if (!share || share.resolved) return

    const picked = answers.find((a) => !a.skipped)
    if (!picked) {
      // Skipped or dismissed — nothing was posted, and the card says exactly
      // that rather than leaving an unanswered question in the thread.
      patchSlackShare(tabId, ps.turnId, { resolved: { outcome: "cancelled" } })
      return
    }
    // A typed answer has no `value`; take the text (minus any leading '#') as
    // the channel name, which the server matches exactly like a picked one.
    const answer = (picked.value ?? picked.answer ?? "").trim()
    if (!answer) {
      patchSlackShare(tabId, ps.turnId, { resolved: { outcome: "cancelled" } })
      return
    }

    if (ps.kind === "target") {
      const target = (share.preview.candidates ?? [])
        .find((c) => `${c.type}-${c.id}` === answer)
      if (!target) return
      await repreviewSlackShare(tabId, ps.turnId, target)
      return
    }

    patchSlackShare(tabId, ps.turnId, { busy: true })
    try {
      const preview = await slackShareApi.preview(share.ref, {
        channel: answer.replace(/^#/, ""),
      })
      patchSlackShare(tabId, ps.turnId, { busy: false, preview })
      // A typed channel that still doesn't resolve asks again rather than
      // silently dropping the share.
      const next = slackShareQuestionFor(preview)
      if (next) {
        setTabs((prev) => prev.map((t) => t.id === tabId
          ? { ...t, pendingShare: { turnId: ps.turnId, ...next } }
          : t))
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      patchSlackShare(tabId, ps.turnId, {
        busy: false, resolved: { outcome: "failed", error: msg },
      })
    }
  }, [patchSlackShare, repreviewSlackShare])

  /** Dismissing the question settles the share as NOT SENT. Deliberate: an
   *  abandoned question would otherwise leave a thread whose last word is
   *  "here's what I'll post" about a message that never went anywhere. */
  const cancelShareQuestion = useCallback((tabId: string) => {
    const ps = tabsRef.current.find((t) => t.id === tabId)?.pendingShare
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, pendingShare: undefined } : t))
    if (ps) patchSlackShare(tabId, ps.turnId, { resolved: { outcome: "cancelled" } })
  }, [patchSlackShare])

  /** The one place anything is actually posted to Slack. */
  /** The assign batch's ONE landing: the popup collected every pick (owner
   *  directive — finish all the questions before anything is sent), and only
   *  now do the writes happen, each through the ordinary fields endpoint. The
   *  summary posts as its own agent turn on the flow's conversation entry. */
  const completeAssign = useCallback(async (
    tabId: string, answers: PopupAnswer[],
  ) => {
    const tab = tabsRef.current.find((t) => t.id === tabId)
    const pa = tab?.pendingAssign
    if (!pa) return
    // Close the batch first — the popup is spent; the writes ride the tab's
    // busy state, not a half-open stepper.
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, pendingAssign: undefined } : t))
    setBusyTabs((prev) => addToSet(prev, tabId))
    const applied = [...pa.applied]
    const failed: string[] = []
    let skipped = 0
    try {
      for (let i = 0; i < pa.questions.length; i++) {
        const q = pa.questions[i]
        const a = answers[i]
        // A multi-pick answer carries EVERY tick on `picks` — one option (and
        // one write) per pick. A single-pick answer resolves to exactly one
        // option, same lookup as always: by stable value first, label second.
        const chosen: TicketAssignQuestion["options"] = []
        if (a && !a.skipped && a.answer) {
          if (q.multi && a.picks?.length) {
            for (const p of a.picks) {
              const opt =
                (p.value != null ? q.options.find((o) => o.value === p.value) : undefined) ??
                q.options.find((o) => o.label === p.label)
              if (opt) chosen.push(opt)
            }
          } else {
            const opt =
              (a.value != null ? q.options.find((o) => o.value === a.value) : undefined) ??
              q.options.find((o) => o.label === a.answer)
            if (opt) chosen.push(opt)
          }
        }
        if (!chosen.length) { skipped += 1; continue }
        for (const opt of chosen) {
          const pair = q.fixed.kind === "ticket"
            ? { key: q.fixed.ticket_key, title: q.fixed.ticket_title, assignee: opt.assignee }
            : { key: opt.value, title: opt.label, assignee: q.fixed.assignee }
          if (!pair.assignee) { skipped += 1; continue }
          try {
            await ticketDataApi.saveFields(pair.key, { assignee: pair.assignee })
            applied.push(`“${pair.title}” → ${pair.assignee.display_name || pair.assignee.email || "them"}`)
          } catch {
            failed.push(pair.title)
          }
        }
      }
      const lines: string[] = []
      if (applied.length) lines.push(`All set — assigned:\n${applied.map((l) => `- ${l}`).join("\n")}`)
      if (skipped) lines.push(`${skipped === 1 ? "One ticket was" : `${skipped} tickets were`} left as they are.`)
      if (failed.length) lines.push(`I couldn't save ${failed.map((t) => `“${t}”`).join(", ")} — try those from the ticket itself.`)
      // Nothing landed and nothing broke → everything was skipped; say that
      // plainly instead of a bare skip count with no verdict.
      const summary = !applied.length && !failed.length
        ? "No assignments made — everything was skipped, so the tickets keep their current owners."
        : lines.join("\n\n")
      const reply = {
        answer: summary, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse
      const noteId =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      setTabs((prev) => prev.map((t) => t.id === tabId
        ? { ...t, thread: [...t.thread, { id: noteId, query: "", reply }] }
        : t))
      finalizeConversationTurn(pa.turnId, { reply }, tabId)
    } finally {
      setBusyTabs((prev) => removeFromSet(prev, tabId))
    }
  }, [finalizeConversationTurn])

  /** The assign popup's × — close the stepper. Nothing has been written from
   *  it (the batch only submits on completion), so there is nothing to report:
   *  the explicit pairs the plan applied are already in the flow's reply. */
  const cancelAssign = useCallback((tabId: string) => {
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, pendingAssign: undefined } : t))
  }, [])

  // Same-tab generation: a PRD command typed in a REGULAR chat tab generates the
  // PRD in THAT tab's artifacts panel — the conversation that motivated it stays
  // on screen next to the document — instead of spawning a new tab. Only a
  // plain, PRD-less chat tab qualifies: a tab already bound to a PRD
  // (prd/prdId/generating) keeps its binding (one PRD per tab — a new-topic
  // command there still opens its own tab), and a brief-insight tab (briefMeta)
  // keeps its insight→PRD flow (the chatInsightState effect stamps the INSIGHT's
  // prd id onto the tab, which would fight an unrelated task-PRD). A
  // still-hydrating resumed tab is skipped too (its background thread fetch
  // would race the seeded command turn). No active tab (landing / brief
  // surface) → undefined → the command opens its own tab as before.
  const reusableActiveTab = useCallback((): ChatTab | undefined => {
    const t = tabsRef.current.find((x) => x.id === activeTabIdRef.current)
    return t && t.prd == null && t.prdId == null && !t.prdGenerating &&
      t.briefMeta == null && !t.hydrating
      ? t : undefined
  }, [])

  // ── Standalone ticket sets: the chat entry point ───────────────────────────
  // "Break this into tickets" in a chat with NO PRD. Before this the request
  // fell through to the ask agent, which answered with the user-stories skill's
  // raw markdown — a wall of ticket bodies in a chat bubble that could not be
  // pushed to Jira, reopened, or found again. It now produces a durable
  // `ticket_sets` artifact that reads in the panel.
  //
  // Tabs whose ticket set has already been opened ON THIS VISIT. Same posture
  // (and the same retirement point) as reportsAutoOpenedRef: leaving a tab
  // retires the claim, so coming back to the thread shows its tickets again,
  // while a manual close sticks for as long as you stay.
  const ticketSetAutoOpenedRef = useRef<Set<string>>(new Set())

  /** Kick off ONE run for `tabId` and own its whole lifecycle on the tab.
   *
   *  The latch is written on the same commit as the kick-off, before any await,
   *  because that is the window the double-generation guard has to cover: two
   *  sends a second apart both read `ticketSetRunning` before either had come
   *  back from the network. */
  const startTicketSetRun = useCallback((
    tabId: string,
    task: string,
    seed: { turnId: string; title: string; query: string } | null,
    /** The uploaded TICKET format the user named, off the intent envelope.
     *  Undefined — the normal case, and every RE-RUN — means the company's
     *  active ticket format. A re-run deliberately does not carry it: the
     *  original envelope is long gone by then, and inventing one would be a
     *  guess about what was asked for rather than a record of it. */
    artifactTemplateId?: string | null,
  ) => {
    setTabs((prev) => prev.map((t) => t.id === tabId
      ? { ...t, ticketSetRunning: true, ticketSetStatus: "generating", ticketSetTask: task }
      : t))
    // This IS the tab's ticket-set open, so the thread-resume probe must not
    // also fire and put a second reader on the same row.
    ticketSetAutoOpenedRef.current.add(tabId)
    void (async () => {
      // The set is stamped with its thread AT CREATION — a `ticket_sets` row has
      // no back-patch route, unlike a PRD (conversationsApi.update) — so the
      // conversation has to exist before the create call goes out, or the set is
      // orphaned from the chat that asked for it and neither the resume nor the
      // Artifacts row can name it. `ensureConversation` shares the very same
      // in-flight create the turn persistence just fired (create-once per tab),
      // so awaiting it costs at most the remainder of one already-issued request
      // and never mints a second conversation. Null on failure → an unlinked
      // set, which still generates and still reads in the panel.
      const convId =
        tabsRef.current.find((t) => t.id === tabId)?.dbConvId ??
        (seed ? await persistence.ensureConversation(tabId, seed) : null)
      // Opened HERE rather than before the await so the panel and the runner's
      // first frame land on the same commit — otherwise the Tickets tab slides
      // out over the "generate a PRD first" empty state for the length of one
      // conversation create. Never yank the panel out from under another tab.
      if (activeTabIdRef.current === tabId) openContentPanel("tickets")
      const result = await runTicketSetGeneration(
        task, convId ?? null, setContent, artifactTemplateId,
      )
      if (result.ok) {
        setTabs((prev) => prev.map((t) => t.id === tabId
          ? { ...t, ticketSetRunning: false, ticketSetId: result.set.id, ticketSetStatus: "ready" }
          : t))
        // The thread's record of what got built — the same agent-only summary
        // turn every other artifact posts. The backend accepts `ticket_set`
        // (routes/artifacts.py::ChatSummaryIn) and renders the ROSTER as the
        // content, so the summary describes the work, not the JSON.
        postSummaryRef.current?.(tabId, "ticket_set", result.set.id)
        return
      }
      setTabs((prev) => prev.map((t) => t.id === tabId
        ? { ...t, ticketSetRunning: false, ticketSetStatus: "failed" }
        : t))
      // A KIND, never a message: nothing off the wire reaches the screen.
      showToast("Tickets unavailable", TICKET_SET_FAILURE_TOAST[result.kind])
    })()
  }, [setContent, showToast, openContentPanel, persistence])

  /** The chat's "generate tickets" command on a tab with no PRD.
   *
   *  Optimistic-first, the same rule the PRD command flows follow: the ack turn
   *  renders on THIS commit and every network call happens after it, so the
   *  composer never clears into a void. */
  /** A compact transcript of a tab's thread, for grounding a generated
   *  document. Newest turns last and the whole thing bounded, because this is
   *  prompt input: an unbounded thread would push the actual request out of
   *  the model's attention, and the oldest turns are the least likely to be
   *  what "this" refers to. */
  const threadContextFor = useCallback((tabId: string): string => {
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (!tab) return ""
    const parts: string[] = []
    for (const turn of tab.thread) {
      if (turn.query) parts.push(`Q: ${turn.query}`)
      const answer = turn.reply?.answer
      if (answer) parts.push(`A: ${answer}`)
    }
    const joined = parts.join("\n\n")
    const MAX = 12_000
    return joined.length <= MAX ? joined : `…\n\n${joined.slice(-MAX)}`
  }, [])

  const ticketSetCommandFlow = useCallback((
    seedQuery: string,
    task: string,
    /** The uploaded TICKET format the user named, off the intent envelope. */
    artifactTemplateId?: string | null,
  ) => {
    const inTab = reusableActiveTab()
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const ack: AskResponse = {
      answer: TICKET_SET_ACK,
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    const seedTurn: ThreadTurn = { id: turnId, query: seedQuery, reply: ack }
    const handle = seedQuery.length > 40 ? `${seedQuery.slice(0, 37)}…` : seedQuery
    let tabId: string
    if (inTab) {
      tabId = inTab.id
      setTabs((prev) => prev.map((t) => t.id === inTab.id
        ? {
            ...t,
            // First message in a placeholder "New chat" tab → take the real
            // title from the command, exactly as submitAsk's own rename does.
            title: t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? handle : t.title,
            thread: [...t.thread, seedTurn],
          }
        : t))
      setDraft("")
    } else {
      // No reusable tab (the landing, the brief tab, or a PRD/insight tab whose
      // binding must not be disturbed) → the command opens its own chat tab.
      tabId = openTab(handle, [seedTurn])
    }
    // Rail + Supabase, so the exchange survives a reload like any other turn.
    pushPendingConversation(turnId, seedQuery, tabId)
    void finalizeConversationTurn(turnId, { reply: ack }, tabId)
    startTicketSetRun(tabId, task, {
      turnId,
      title: seedQuery.length > 52 ? `${seedQuery.slice(0, 49)}…` : seedQuery,
      query: seedQuery,
    }, artifactTemplateId)
  }, [
    reusableActiveTab, openTab, pushPendingConversation, finalizeConversationTurn,
    startTicketSetRun,
  ])

  /** Write a team document from this chat and open it in the right panel.
   *
   *  Mirrors `ticketSetCommandFlow`: seed a turn with an acknowledgement, put
   *  it on the rail and in Supabase, THEN start the work — so the exchange
   *  survives a reload and reads like every other command. */
  const documentCommandFlow = useCallback((
    seedQuery: string,
    envelope: ChatIntentEnvelope,
  ) => {
    const inTab = reusableActiveTab()
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const kind = envelope.artifact_kind?.trim() || "document"
    const ack: AskResponse = {
      answer: `Writing your ${kind} — it will open in the panel on the right.`,
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    const seedTurn: ThreadTurn = { id: turnId, query: seedQuery, reply: ack }
    const handle = seedQuery.length > 40 ? `${seedQuery.slice(0, 37)}…` : seedQuery
    let tabId: string
    let convId: number | null = null
    if (inTab) {
      tabId = inTab.id
      convId = inTab.dbConvId ?? null
      setTabs((prev) => prev.map((t) => t.id === inTab.id
        ? {
            ...t,
            title: t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? handle : t.title,
            thread: [...t.thread, seedTurn],
          }
        : t))
      setDraft("")
    } else {
      // Sent from the landing or a PRD/insight tab whose binding must not be
      // disturbed → the command opens its own chat tab, rather than writing a
      // document that belongs to no thread at all.
      tabId = openTab(handle, [seedTurn])
    }
    pushPendingConversation(turnId, seedQuery, tabId)
    void finalizeConversationTurn(turnId, { reply: ack }, tabId)

    void (async () => {
      try {
        // THE CONVERSATION HAS TO EXIST BEFORE THE DOCUMENT DOES.
        //
        // `convId` above is read synchronously off the tab, and on a tab's
        // FIRST message that is null: `pushPendingConversation` fires the
        // create and deliberately does not await it. So the most common path
        // there is — ask a brand-new chat for a leadership update — stored the
        // document with `conversation_id` NULL, orphaning it from the thread
        // that asked for it. `useThreadDocumentSync` then could not re-attach
        // it on reload or on coming back to the thread, and the panel had
        // nothing to show.
        //
        // Exactly the defect #969 fixed for reports and the ticket-set flow
        // fixed for its own rows, with the same instrument: `ensureConversation`
        // shares the very same in-flight create the turn persistence just fired
        // (create-once per tab), so awaiting it costs at most the remainder of
        // one already-issued request and never mints a second conversation. It
        // resolves null on failure, which leaves an unlinked document — still
        // generated, still readable in the library — rather than no document.
        const attachTo = convId ?? await persistence.ensureConversation(tabId, {
          turnId,
          // THE SAME TITLE `pushPendingConversation` WOULD HAVE USED, not the
          // tab's `handle`. Whichever of the two calls wins the create race
          // names the stored row, and this one now usually wins — so a
          // different truncation here (37 chars vs 49) would silently rename
          // the conversation in Chat history for this flow alone, leaving the
          // in-session rail and the reloaded list disagreeing about the same
          // thread.
          title: seedQuery.length > 52 ? `${seedQuery.slice(0, 49)}…` : seedQuery,
          query: seedQuery,
        })
        const created = await customArtifactsApi.generate({
          kind,
          task: envelope.task?.trim() || seedQuery,
          // THE GROUNDING. Without this the generator takes its
          // "CONTEXT: none was supplied" branch and writes a structural
          // skeleton that lists what it does not know — for a request whose
          // whole subject was discussed in the thread above it. The planner's
          // `task` is a brief, not the evidence behind it.
          context: threadContextFor(tabId),
          conversation_id: attachTo,
        })
        // NEVER OPEN THIS TAB'S DOCUMENT OVER SOMEONE ELSE'S THREAD. The
        // create + generate round trips mean the user can have moved on by
        // now, and this pair is unconditional: it would put chat A's document
        // in front of whoever is reading chat B.
        //
        // The clear-on-switch used to paper over that — B gaining its own
        // conversation id wiped the stray id — but a conversation coming into
        // existence is no longer treated as a switch (that is the fix above),
        // so the guard has to be stated where the assumption actually lives.
        // The same rule `startTicketSetRun` already follows.
        //
        // Nothing is lost by skipping it: the document is now attached to its
        // conversation, so returning to this thread re-opens it through
        // `useThreadDocumentSync`.
        if (activeTabIdRef.current !== tabId) return
        setContent({ documentId: created.id, documentGenerating: true })
        openContentPanel("document")
      } catch {
        showToast(
          "Couldn't start that document",
          "Please try again, or create one from Artifacts.",
        )
      }
    })()
  }, [
    reusableActiveTab, openTab, pushPendingConversation, finalizeConversationTurn,
    setContent, openContentPanel, showToast, threadContextFor, persistence,
  ])

  /** The reply-footer button: reopen a finished set, or re-run a failed one. */
  const handleTicketSetAction = useCallback(async (tabId: string) => {
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (!tab) return
    if (tab.ticketSetStatus === "failed") {
      // Re-run from the ORIGINAL request. In-session that is on the tab; after
      // a reload it is read back off the row (`source_text`), because the
      // transient copy is deliberately not persisted.
      let task = tab.ticketSetTask?.trim() || content.ticketSet?.sourceText?.trim() || ""
      if (!task && tab.ticketSetId != null) {
        const { ticketSetsApi } = await import("../../../lib/api")
        task = await ticketSetsApi.get(tab.ticketSetId)
          .then((r) => r.source_text?.trim() ?? "")
          .catch(() => "")
      }
      if (!task) {
        showToast(
          "Ask again in the chat",
          "The original request isn't available any more — say what to break into tickets and I'll re-run it.",
        )
        return
      }
      startTicketSetRun(tabId, task, null)
      return
    }
    if (tab.ticketSetId == null) return
    // Always re-read the set rather than trusting whatever is in shared content:
    // the panel is global, and opening a PRD in the meantime clears the slice.
    setContent({ ticketSetStandalone: false })
    openContentPanel("tickets")
    void loadTicketSet(tab.ticketSetId, setContent)
  }, [content.ticketSet, setContent, openContentPanel, showToast, startTicketSetRun])

  const prdCommandFlow = useCallback((
    seedQuery?: string,
    taskOverride?: string | null,
    artifactTemplateId?: string | null,
  ) => {
    // A command naming a SPECIFIC task ("generate a PRD for dark mode") builds
    // the PRD from the user's own words. A GENERIC "generate a PRD" (no topic) is
    // seeded from THIS conversation — the user's turns in the active tab — so the
    // PRD is about what was actually discussed. (Previously a bare command
    // defaulted to the brief's top insight, which served an unrelated PRD.) With
    // no conversation to seed from, we ask for a topic rather than open junk.
    // `taskOverride` carries the classifier-extracted task on the LLM-fallback
    // path, where the regex extractor (prdCommandTask) by definition can't parse
    // the phrasing.
    const task = taskOverride ?? (seedQuery ? prdCommandTask(seedQuery) : null)
    const activeTabNow = tabsRef.current.find((t) => t.id === activeTabIdRef.current)
    const effectiveTask = task || conversationToPrdTask(activeTabNow?.thread ?? [])
    // What the PRD grounds on: documents attached earlier in this conversation
    // AND the conversation itself — the agent's replies included, since that is
    // where a fetched ticket or finding actually lives. (The doc-on-the-SAME-
    // message case routes to importPrdCommandFlow before this flow is ever
    // called, and keeps its own replace-grounding behaviour.)
    const sourceDocs = prdGroundingDocs(activeTabNow?.thread ?? [])
    if (!effectiveTask) {
      // No explicit topic and no conversation to ground it — ask for a topic.
      // This branch stays synchronous (no network), so it never has the gap.
      showToast(
        "What should the PRD cover?",
        "Tell me the topic — e.g. \"generate a PRD for magic-link sign-in\" — or describe the problem first and I'll build one from our chat.",
      )
      return
    }
    // The title is a placeholder derived from the task until the backend's real
    // title lands (openPrdInTab renames the tab once generateFromTask resolves).
    const placeholder = effectiveTask.length > 37 ? `${effectiveTask.slice(0, 37)}…` : effectiveTask
    const inTab = reusableActiveTab()
    const req: LocalPrdTabRequest = {
      title: `PRD · ${placeholder}`,
      seedQuery,
      ...(inTab ? { inTabId: inTab.id } : {}),
      source: {
        kind: "generateTask",
        task: effectiveTask,
        ...(sourceDocs.length ? { sourceDocs } : {}),
        ...(artifactTemplateId ? { artifactTemplateId } : {}),
      },
    }
    const tabId = openPrdInTab(req)
    seedCommandTurn(req, tabId)
  }, [openPrdInTab, reusableActiveTab, seedCommandTurn, showToast])

  // A command phrasing over an ATTACHED DOCUMENT is the chat entry to the
  // PRD-import flow: upload the doc to POST /v1/prd/import — the same conversion
  // the Artifacts "Upload PRD" button uses (parse to text, faithful re-layout
  // into the chat-PRD format) — then open the imported PRD as its own chat tab.
  // With `openTickets` ("convert this PRD into tickets") the panel lands on the
  // Tickets tab once the PRD is ready, which generates user stories for it.
  //
  // OPTIMISTIC-FIRST (the reported latency bug): the previous version awaited
  // prdApi.importDoc BEFORE opening the tab, so a "generate PRD" over a big deck
  // cleared the composer and showed NOTHING for several seconds. We now hand the
  // UNSTARTED import to openPrdInTab, which seeds the chat turn + shows the panel
  // spinner on THIS commit and runs importDoc inside its async block (network
  // AFTER the render). The placeholder title is the file name until the backend's
  // real title lands.
  const importPrdCommandFlow = useCallback((
    file: File,
    opts: {
      openTickets: boolean; seedQuery?: string
      /** The uploaded format the user named. Easy to miss on THIS path and the
       *  most important one to get right: attaching a file to "create a PRD
       *  using our Acme format" dispatches an import, so a format dropped here
       *  is a document silently written in a different one. */
      artifactTemplateId?: string | null
    },
  ) => {
    const inTab = reusableActiveTab()
    const req: LocalPrdTabRequest = {
      title: file.name,
      seedQuery: opts.seedQuery,
      ...(inTab ? { inTabId: inTab.id } : {}),
      source: {
        kind: "importDoc", file, company: activeCompany,
        openTickets: opts.openTickets,
        ...(opts.artifactTemplateId ? { artifactTemplateId: opts.artifactTemplateId } : {}),
      },
    }
    const tabId = openPrdInTab(req)
    seedCommandTurn(req, tabId)
  }, [activeCompany, openPrdInTab, reusableActiveTab, seedCommandTurn])

  // ── "Open the PRD for X" ───────────────────────────────────────────────────
  // The ACTION half of the open_artifact envelope. The backend has already done
  // the hard part (this is an OPEN, not a generate; here is the document, or
  // here are the two it could be, or here is nothing) — everything below is
  // about putting the named artifact in the right-hand panel of the CHAT, which
  // is the part that was missing: the old flow answered with the document
  // reconstructed as chat text and never opened a panel at all.

  /** Put ONE artifact in the panel. Returns false for a candidate that carries
   *  no openable id (nothing happens rather than an empty panel).
   *
   *  `seedQuery` seeds the user's typed command as a thread turn. A CHIP click
   *  passes none — it is direct manipulation of the panel, not another message,
   *  so it must not put words in the user's mouth or spend an ask. */
  const openArtifactInPanel = useCallback(
    (candidate: OpenArtifactCandidate, seedQuery?: string): boolean => {
      if (candidate.type === "evidence") {
        if (candidate.brief_id == null || candidate.insight_index == null) return false
        // The SAME binding guard the PRD branch uses. Pinning the active tab
        // unconditionally let an evidence open hijack a tab already holding a
        // PRD: openPrdInTab's evidence branch writes `evidenceOnly` +
        // `evidenceDetail` for insight B onto a tab whose prdId is still A, so
        // the panel renders B's evidence beside A's document and the tab is
        // flagged evidence-only while holding a prd id. `reusableActiveTab`
        // declines exactly that tab, and the open gets a chat of its own.
        const inTab = reusableActiveTab()
        const req: LocalPrdTabRequest = {
          title: candidate.title || "Evidence",
          ...(seedQuery ? { seedQuery } : {}),
          ...(inTab ? { inTabId: inTab.id } : {}),
          source: {
            kind: "evidence",
            meta: { briefId: candidate.brief_id, insightIndex: candidate.insight_index },
            detail: null,
          },
        }
        const tabId = openPrdInTab(req)
        seedCommandTurn(req, tabId)
        return true
      }
      const prdId = candidate.prd_id ?? candidate.id
      if (prdId == null) return false
      // The PRD's own THREAD outranks a panel-beside-this-chat open (owner
      // decision, 2026-08-14): when the conversation that produced the
      // document survives, "open the PRD" means going back to that chat —
      // history restored, PRD panel over it — exactly like clicking the same
      // row on the Artifacts screen. Both halves must be present (a
      // title-less id means the chat row is gone), and an uploaded or
      // brief-generated PRD carries neither, so it keeps today's panel-only
      // open — never a fake history.
      if (candidate.conversation_id != null && candidate.conversation_title) {
        try {
          localStorage.setItem("sprntly_resume_conv", JSON.stringify({
            dbId: candidate.conversation_id,
            title: candidate.conversation_title,
            fallbackTurns: [],
            prdId,
          }))
          checkResume()
          return true
        } catch { /* storage unavailable → the panel-only open below */ }
      }
      // Reuse BY PRD ID, never by title. A tab already holding this document
      // wins over the tab the user is typing in, so opening a PRD that is
      // already open focuses it instead of spawning a second tab for the same
      // id — the duplicate-tab bug #1039 fixed for `?prd=` deep links, which
      // this path would otherwise reintroduce from a different entry point (the
      // titles here are real document titles, so a title match would look like
      // it works right up until two documents share a name).
      const holder = tabsRef.current.find(
        (t) => t.prdId === prdId || t.prd?.prd_id === prdId,
      )
      // Otherwise: the CHAT the user is in, so the panel opens beside the
      // conversation that asked for it (the stated requirement) rather than in
      // a tab of its own. `reusableActiveTab` declines a tab already bound to a
      // different PRD/insight, which must not be repointed.
      const inTab = holder ?? reusableActiveTab()
      // Is the document ALREADY cached on the tab we're about to open into? Then
      // openPrdInTab returns straight from that cache and never reaches the
      // async block, so the acknowledgment has to ride the seed turn instead of
      // being deferred — see LocalPrdTabRequest.ackInline for what goes wrong
      // when the two disagree. Only `holder` can satisfy this: `reusableActiveTab`
      // returns tabs with no PRD by definition.
      const ackInline = holder?.prd?.prd_id === prdId
      const req: LocalPrdTabRequest = {
        title: candidate.title ? `PRD · ${candidate.title}` : "PRD",
        ...(seedQuery ? { seedQuery } : {}),
        ...(inTab ? { inTabId: inTab.id } : {}),
        ...(ackInline ? { ackInline: true } : {}),
        source: {
          kind: "load",
          prdId,
          // The finding this PRD came from, so the panel's Evidence tab has
          // something to load (it reads `content.prdMeta` and fetches by
          // (briefId, insightIndex) — with null meta that tab is simply dead,
          // while the SAME document opened from Artifacts worked).
          //
          // Only when the backend says the pair is real: a chat / ideation /
          // uploaded PRD carries insight_index 0 as a storage sentinel, and
          // passing that would load the brief's first finding under a document
          // that has nothing to do with it. Those PRDs genuinely have no
          // insight, so null is the correct answer for them, not a limitation.
          meta:
            candidate.brief_anchored &&
            candidate.brief_id != null &&
            candidate.insight_index != null
              ? { briefId: candidate.brief_id, insightIndex: candidate.insight_index }
              : null,
        },
      }
      const tabId = openPrdInTab(req)
      seedCommandTurn(req, tabId)
      return true
    },
    [openPrdInTab, reusableActiveTab, seedCommandTurn, checkResume],
  )

  /** Post an assistant turn that opens NOTHING — the ambiguous and not-found
   *  halves of the contract. Mirrors ticketSetCommandFlow's seeding so the
   *  exchange lands in the rail and Supabase like any other turn.
   *
   *  It appends to whatever chat the user is in — including a PRD-bound one —
   *  because unlike the command flows this turn is pure text: it binds nothing
   *  to the tab, so there is no binding to protect and no reason to answer a
   *  question in a tab the user wasn't looking at. Only the thread-less brief
   *  tab (and no tab at all) spawns a chat, matching submitAsk. */
  const postOpenArtifactReply = useCallback(
    (seedQuery: string, answer: string, candidates: OpenArtifactCandidate[]) => {
      const activeId = activeTabIdRef.current
      const inTab = activeId && activeId !== BRIEF_TAB_ID
        ? tabsRef.current.find((t) => t.id === activeId)
        : undefined
      const turnId =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      const reply: AskResponse = {
        answer, sources: [], follow_ups: [], key_points: [], citations: [],
        confidence: 1, unanswered: "",
      } as AskResponse
      const seedTurn: ThreadTurn = {
        id: turnId,
        query: seedQuery,
        reply,
        ...(candidates.length ? { openCandidates: candidates } : {}),
      }
      const handle = seedQuery.length > 40 ? `${seedQuery.slice(0, 37)}…` : seedQuery
      let tabId: string
      if (inTab) {
        tabId = inTab.id
        setTabs((prev) => prev.map((t) => t.id === inTab.id
          ? {
              ...t,
              title: t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? handle : t.title,
              thread: [...t.thread, seedTurn],
            }
          : t))
        setDraft("")
      } else {
        tabId = openTab(handle, [seedTurn])
      }
      pushPendingConversation(turnId, seedQuery, tabId)
      // Only the prose is persisted: the chips are a live affordance, and the
      // conversation record is what the assistant SAID.
      void finalizeConversationTurn(turnId, { reply }, tabId)
    },
    [openTab, pushPendingConversation, finalizeConversationTurn],
  )

  /** The whole open_artifact dispatch: 1 match opens, 2+ ask, 0 says so — and a
   *  kind this panel can't show says where it DOES live. */
  const openArtifactFlow = useCallback(
    (seedQuery: string, open: OpenArtifactResult) => {
      const noun = open.artifact_type === "evidence" ? "evidence" : "PRD"
      if (open.status === "unsupported_type") {
        // They named a real thing we simply don't render here. Substituting the
        // PRD of the same name would hand over the wrong document with nothing
        // to signal the swap, so name what they asked for and point at where it
        // opens.
        postOpenArtifactReply(
          seedQuery,
          `${UNSUPPORTED_OPEN_KIND[open.artifact_type] ?? "That kind of artifact"} doesn't open in this panel — you'll find it in the Artifacts tab. I can open a PRD or its evidence here.`,
          [],
        )
        return
      }
      if (open.status === "resolved" && open.artifact) {
        if (openArtifactInPanel(open.artifact, seedQuery)) return
        // A match we cannot actually open (no usable id) is a NOT-FOUND from
        // the user's side; saying so beats opening an empty panel.
        postOpenArtifactReply(
          seedQuery,
          `I found "${open.artifact.title}" but couldn't open it — try it from the Artifacts tab.`,
          [],
        )
        return
      }
      if (open.status === "ambiguous") {
        postOpenArtifactReply(
          seedQuery,
          `There's more than one ${noun} matching "${open.query}". Which one did you mean?`,
          open.candidates,
        )
        return
      }
      // not_found. Deliberately does NOT offer to generate one: the user asked
      // to open something, and turning that into a generation is the exact
      // failure this action exists to prevent.
      postOpenArtifactReply(
        seedQuery,
        `I couldn't find a ${noun} for "${open.query}". Nothing was opened — check the Artifacts tab, or tell me to generate one if you'd like it written.`,
        [],
      )
    },
    [openArtifactInPanel, postOpenArtifactReply],
  )

  // ── One artifact-list card → its own thread ────────────────────────────────
  // The chat-side twin of ArtifactsScreen.openArtifact, per kind: an artifact
  // whose chat survives opens THAT thread (history restored, its panel over
  // it); one without a thread opens standalone — panel, page or canvas — and
  // never a fake history. The scope-clearing rules are the Artifacts screen's,
  // for the Artifacts screen's reasons.
  const openChatArtifactItem = useCallback((a: ChatArtifactItem) => {
    const convId = a.source.conversation_id ?? null
    const convTitle = a.source.conversation_title || null
    const writeResume = (prdId: number | null): boolean => {
      if (convId == null || !convTitle) return false
      try {
        localStorage.setItem("sprntly_resume_conv", JSON.stringify({
          dbId: convId, title: convTitle, fallbackTurns: [], prdId,
        }))
        return true
      } catch { return false }
    }
    // Opening anything that isn't a report retires the standalone-report
    // pointer; same for the ticket set on screen (it decides whether the
    // Tickets tab appears at all) — see ArtifactsScreen.openArtifact.
    if (a.type !== "report") {
      setContent({ reportFocusId: null, reportFocusStandalone: false })
    }
    if (a.type !== "ticket_set") {
      setContent({ ticketSet: null, ticketSetGenerating: false, ticketSetStandalone: false })
    }
    if (a.type === "prd" || a.type === "evidence") {
      // openArtifactInPanel already carries BOTH halves — the resume-first
      // thread open for a PRD with a surviving chat, and the panel fallback —
      // so the card hands it a candidate and inherits the same behavior.
      const opened = openArtifactInPanel({
        type: a.type,
        id: a.id,
        title: a.title,
        status: a.status,
        prd_id: a.open.prd_id ?? null,
        brief_id: a.open.brief_id ?? null,
        insight_index: a.open.insight_index ?? null,
        brief_anchored: a.brief_anchored,
        week_label: a.source.week_label ?? null,
        conversation_id: convId,
        conversation_title: convTitle,
      })
      if (!opened) showToast("Couldn't open artifact", "Try it from the Artifacts tab.")
      return
    }
    if (a.type === "report" && a.open.report_id != null) {
      if (writeResume(a.source.prd_id ?? null)) {
        setPendingReportFocus({ conversationId: convId!, reportId: a.open.report_id })
        checkResume()
        return
      }
      // No surviving chat → the same panel, standalone (ArtifactsScreen's
      // fallback, verbatim).
      setContent({
        conversationId: null,
        reportFocusId: a.open.report_id,
        reportFocusStandalone: true,
      })
      openContentPanel("reports")
      return
    }
    if (a.type === "ticket_set" && a.open.ticket_set_id != null) {
      if (writeResume(null)) {
        setPendingTicketSetFocus({ conversationId: convId!, ticketSetId: a.open.ticket_set_id })
        checkResume()
        return
      }
      setContent({ ticketSetStandalone: true })
      openContentPanel("tickets")
      void loadTicketSet(a.open.ticket_set_id, setContent)
      return
    }
    if (a.type === "custom_artifact" && a.open.custom_artifact_id != null) {
      // A document opens its own PAGE (it is written, not read beside a chat).
      router.push(documentPath(a.open.custom_artifact_id))
      return
    }
    if (a.type === "prototype" && a.open.prd_id != null) {
      router.push(prototypePath(a.open.prd_id))
      return
    }
    showToast("Couldn't open artifact", "Try it from the Artifacts tab.")
  }, [setContent, openArtifactInPanel, setPendingReportFocus, setPendingTicketSetFocus,
      checkResume, openContentPanel, router, showToast])

  /** "What are my PRDs?" → a reply naming the count plus the clickable cards.
   *  Mirrors postOpenArtifactReply's seeding (rail + Supabase persistence, the
   *  prose only — the cards are a live affordance riding the turn). */
  const listArtifactsFlow = useCallback((seedQuery: string, envelope: ChatIntentEnvelope) => {
    const items = envelope.artifact_list ?? []
    const kind = envelope.list_kind && envelope.list_kind !== "all" ? envelope.list_kind : null
    const kindNoun: Record<string, [string, string]> = {
      prd: ["PRD", "PRDs"],
      evidence: ["evidence document", "evidence documents"],
      prototype: ["prototype", "prototypes"],
      report: ["report", "reports"],
      ticket_set: ["ticket set", "ticket sets"],
      custom_artifact: ["document", "documents"],
    }
    const [one, many] = kind ? kindNoun[kind] ?? ["artifact", "artifacts"] : ["artifact", "artifacts"]
    // A HOW-MANY ask leads with the NUMBERS — computed server-side over the
    // whole library, never counted off the capped card list (the reported
    // "12 cards for a today-vs-yesterday question" bug). The cards still
    // render under it as the click-to-open affordance.
    const counts = envelope.list_mode === "count" ? envelope.artifact_counts : null
    // "your N newest", never "the N you've created": the rows are capped
    // (backend cap, or the count the user asked for), so claiming they are
    // everything would be wrong the moment the library outgrows the cap —
    // the reported bug's phrasing half. The asked-for count ALSO names the
    // request back ("your last 5 PRDs"), so an honoured ask is visible.
    const answer = counts
      ? [
          `You've created ${counts.today} ${counts.today === 1 ? one : many} today and ${counts.yesterday} yesterday`,
          counts.total > counts.today + counts.yesterday
            ? ` — ${counts.total} in total.`
            : ".",
          items.length ? ` The newest are below — click one to open it with its chat.` : "",
        ].join("")
      : items.length === 0
        ? `You haven't created any ${many} yet — generate one from a chat or the Top Insights brief and it'll show up here.`
        : items.length === 1
          ? `Here's your most recent ${one} — click it to open it with its chat.`
          : `Here are your ${items.length} newest ${many} — click one to open it with its chat.`
    const activeId = activeTabIdRef.current
    const inTab = activeId && activeId !== BRIEF_TAB_ID
      ? tabsRef.current.find((t) => t.id === activeId)
      : undefined
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const reply: AskResponse = {
      answer, sources: [], follow_ups: [], key_points: [], citations: [],
      confidence: 1, unanswered: "",
    } as AskResponse
    const seedTurn: ThreadTurn = {
      id: turnId,
      query: seedQuery,
      reply,
      ...(items.length ? { artifactList: items } : {}),
    }
    const handle = seedQuery.length > 40 ? `${seedQuery.slice(0, 37)}…` : seedQuery
    let tabId: string
    if (inTab) {
      tabId = inTab.id
      setTabs((prev) => prev.map((t) => t.id === inTab.id
        ? {
            ...t,
            title: t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? handle : t.title,
            thread: [...t.thread, seedTurn],
          }
        : t))
      setDraft("")
    } else {
      tabId = openTab(handle, [seedTurn])
    }
    pushPendingConversation(turnId, seedQuery, tabId)
    void finalizeConversationTurn(turnId, { reply }, tabId)
  }, [openTab, pushPendingConversation, finalizeConversationTurn])

  const submitAsk = useCallback(
    async (rawQuery: string) => {
      const trimmed = rawQuery.trim()
      // A doc-only send (empty ask + attachment) is allowed; a truly empty send is
      // a no-op. Hoisted above the pending-send render below (it used to sit just
      // before the optimistic turn) so an empty send can never strand a
      // placeholder on screen.
      if (trimmed.length < 1 && attachments.length === 0) return
      // Retire the previous turn's next-prompt suggestions RIGHT HERE — the one
      // and only place, so every entry point clears identically.
      //
      // This used to sit ~290 lines further down, after the intent-envelope
      // round trip and after eighteen early returns. A typed send that resolved
      // to `answer` reached it and looked correct; a send that took ANY command
      // branch (edit_prd / tickets / generate_prd) returned before it and left
      // the strip standing for the whole generation. Chip clicks hit that far
      // more often than typing does, because a suggestion is frequently phrased
      // as exactly the kind of request the envelope routes to a command — which
      // is why this read as "chip clicks don't clear" (staging, 2026-08-05).
      //
      // Keyed on activeTabId, not the not-yet-resolved targetTabId: that is what
      // the strip renders from, and a send that spawns a NEW tab gives that tab
      // no suggestions to clear. Synchronous and unconditional — the instant
      // anything is sent, suggestions about the previous turn are stale, and
      // there is no branch where keeping them is right.
      if (activeTabId) clearSuggestions(activeTabId)
      // Show the user's message NOW — before the dispatch decision, which is a
      // network round-trip away. `settlePendingSend()` retires it at every exit
      // below; the branch that wins renders its own real turn in the same commit.
      // See the `pendingSend` declaration for why this isn't a ThreadTurn.
      const askStartedAt = Date.now()
      setPendingSend({
        tabId: activeTabId,
        query: trimmed,
        attachments: attachments.map((a) => ({ name: a.name })),
        startedAt: askStartedAt,
      })
      const settlePendingSend = () => setPendingSend(null)
      // Command phrasings are COMMANDS, not questions for the ask agent —
      // intercept before any tab/ask work. Tickets is checked FIRST: "create
      // tickets from this PRD" matches the PRD rule too, but the user asked for
      // tickets. With a document attached, either phrasing imports the doc as a
      // PRD; "…tickets" additionally lands on the Tickets tab when it's ready.
      const docFile = attachments.find((a) => a.file)?.file ?? null
      // Deictic-edit guard: with NO attached document, an edit-phrased message
      // that points at the OPEN artifact ("make this PRD shorter", "shorten
      // that ticket") beside a PRD tab is a QUESTION about that artifact, not a
      // command to spawn a new one — let it fall through to the ask agent,
      // which is grounded on the tab's PRD (+ evidence/tickets) since #786.
      // With an attachment, "this PRD"/"this document" names the FILE, so the
      // import flows below still run unchanged.
      const activeTab = activeTabId ? tabsRef.current.find((t) => t.id === activeTabId) : undefined
      // The clarify gate is mid-decision on this tab (a PRD command's reply is
      // deferred — see deferredAckRef). A send landing INSIDE that window broke
      // two invariants at once: its user turn queued ahead of the deferred
      // assistant write (inverting the persisted user→assistant pairing the
      // history restore depends on), and a second PRD command would overwrite
      // the tab's deferred-ack entry, persisting one command's reply against
      // the other's turn. The window is a single clarify round-trip (~seconds,
      // under a visible thinking indicator), so hold the message and hand it
      // back rather than letting it corrupt the record.
      if (activeTab?.prdCommandThinking) {
        settlePendingSend()
        setDraft(rawQuery)
        showToast("One moment", "Still working out that PRD request — I'll take your next message in a second.")
        return
      }
      // A ticket run already going on THIS tab. Not a nicety: the insight path
      // deliberately does not dedupe (routes/stories.py), and the insight is an
      // LLM-composed string, so "break this into tickets" and "make tickets for
      // that" are two rows and two multi-minute bills for one request. Only the
      // duplicate ASK is refused — the tab stays fully usable for questions
      // while the run goes — and the message is handed back to the composer
      // rather than dropped, exactly as the PRD guard above does. Returns true
      // when it swallowed the send.
      const ticketSetInFlightGuard = (): boolean => {
        if (!activeTab?.ticketSetRunning) return false
        settlePendingSend()
        setDraft(rawQuery)
        // Bring the run they already have forward instead of just refusing.
        openContentPanel("tickets")
        showToast(
          "Already writing those tickets",
          "That run is still going — it'll land in the panel on the right.",
        )
        return true
      }
      // `isPrdTab` survives the ladder's removal because the ask path below
      // still reads it. The two deictic regexes that sat here did not: they
      // existed only to stop the ladder's own patterns from hijacking a PRD
      // tab, and there are no patterns left to hijack anything.
      const isPrdTab = !!(activeTab && (activeTab.prd || activeTab.prdId != null || activeTab.prdGenerating))
      // Clarify-first answers: this tab's PRD task is parked behind the
      // sufficiency gate's questions — the message IS the answers (or a
      // "generate now" skip), never a fresh command/ask. Checked before every
      // other branch so an answer like "make it for enterprise admins" can't
      // be misread as an edit or command phrasing.
      if (activeTab?.pendingClarify && !docFile) {
        const { task, sourceDocs, turnId } = activeTab.pendingClarify
        const skipped = CLARIFY_SKIP_RE.test(trimmed)
        const combined = skipped
          ? task
          : `${task}\n\nAdditional details from the user:\n${trimmed}`
        // The prose path settles the batch too, so answering in the composer
        // keeps the same formatted record the card leaves behind. There's no
        // per-question mapping in free text, so it resolves as "chat" (or
        // "skip" for a bare "generate now") rather than inventing one.
        markClarifyResolved(activeTab.id, turnId, { answers: [], mode: skipped ? "skip" : "chat" })
        const { prdApi } = await import("../../../lib/api")
        runClarifiedGeneration(prdApi, activeTab.id, combined, sourceDocs, trimmed)
        settlePendingSend()
        return
      }
      // ── The planner decides. Nothing in this file guesses. ───────────────
      // EVERY message goes to the backend first (POST /v1/chat/intent, now
      // backed by the Ask Planner) and the verdict decides which flow runs.
      // This browser used to decide for itself, with a ladder of regexes over
      // the newest message — `isPrdCommand`, `isTicketsCommand`,
      // `isPrdEditCommand`, `mentionsPrd` plus a haiku classifier behind it.
      // That ladder is gone, and its removal is the point: a regex deciding to
      // GENERATE A PRD means an oddly-phrased question spends minutes and real
      // money building a document nobody asked for, and no amount of tuning the
      // pattern fixes the class of bug. The planner reads the whole message and
      // the whole thread, so it does not have that failure mode.
      //
      // FAILURE MODE ON PURPOSE: if the call fails, this falls through to the
      // grounded ask path — the question gets ANSWERED. It does not fall back
      // to guessing. The worst case is that a genuine "write me a PRD" is
      // answered as a question and the user asks again; the alternative is
      // exactly the accidental generation this removal exists to stop.
      //
      // A "/skill …" message is EXPLICIT intent with its own backend fast-path
      // (qa_agent's slash route) — the user named the skill, so there is
      // nothing to infer and no call to spend.
      // Attachment text, read ONCE and read EARLY — before the planner is asked
      // to decide, because the decision depends on it.
      //
      // This used to run after the intent call, and the planner therefore judged
      // "generate a PRD" with a deck attached as a request with no subject: it
      // returned generate_prd at 0.5 confidence, the action floor (0.6) downgraded
      // it to `answer`, and the user got prose instead of a document. The ask
      // worker then planned the SAME turn again a few seconds later, this time
      // with the extracted text in the question, and scored it 0.97 — the right
      // answer, arriving after the client had already committed to the wrong one.
      //
      // Extracting first costs no extra wall-clock: this work was always on the
      // critical path, just later in it. Best-effort by design — a failure here
      // yields null and the message goes to the planner bare, exactly as before;
      // the real extraction below keeps its own rollback/toast handling and is
      // still the thing that decides whether the send can proceed.
      let earlyExtracted: (string | null)[] | null = null
      if (attachments.length > 0) {
        earlyExtracted = await Promise.all(
          attachments.map((a) =>
            a.content
              ? Promise.resolve<string | null>(a.content)
              : a.file
              ? askApi
                  .extractFile(a.file)
                  .then((r) => r.markdown.slice(0, 50000))
                  .catch(() => null)
              : Promise.resolve<string | null>(a.content ?? null),
          ),
        )
      }

      if (!trimmed.startsWith("/")) {
        const tabPrdId = (activeTab?.prd?.prd_id ?? activeTab?.prdId) ?? null
        // The planner sees what the answer path will see. Same `[Attached files]`
        // framing and the same 100k clamp the send below uses, so the question
        // the plan was made for and the question that gets executed match — which
        // is also what lets the worker reuse this plan instead of buying another.
        const attachedForIntent = earlyExtracted?.some((t) => t)
          ? attachments
              .map((a, i) => `--- ${a.name} ---\n${earlyExtracted![i] ?? ""}`)
              .join("\n\n")
              .slice(0, 100000)
          : null
        const intentMessage = attachedForIntent
          ? `${trimmed}\n\n[Attached files]\n${attachedForIntent}`
          : trimmed
        const envelope = await import("../../../lib/api")
          .then(({ chatIntentApi }) =>
            chatIntentApi.resolve(intentMessage, {
              conversationId: activeTab?.dbConvId ?? null,
              prdId: tabPrdId,
              hasAttachments: attachments.length > 0,
            }),
          )
          .catch(() => null)
        if (envelope) {
          // THE QUIET FAILURE, and the more dangerous of the two. The endpoint
          // fails open to `answer` when the model is unreachable — correct, a
          // dead planner must never break a send — but with it down NO action
          // can be recognised, so every command in the product silently turns
          // into a chat reply. The message still gets answered, so nothing
          // looks broken; asking for things simply stops working, and the only
          // evidence is a line in a container log. Say it out loud instead.
          const intentNotice = providerNoticeFromEnvelope(envelope)
          if (intentNotice) {
            showToast(
              providerNoticeTitle(intentNotice),
              `${intentNotice.message} Until then, commands like "write a PRD" or "share this on Slack" will be answered as ordinary questions.`,
              undefined,
              { persist: true },
            )
          }
          // The intent→executor SWITCH itself is lifted into the shared
          // `dispatchChatIntent` primitive — the private project chat reuses
          // the SAME switch. ChatScreen supplies today's inline flows as
          // executors, byte-identical to the ladder they replace; the doc/tab
          // guards that decide WHICH flow to run stay HERE (ChatScreen-local
          // UI state dispatchChatIntent knows nothing about), not inside the
          // shared primitive.
          const targetPrdId =
            !docFile && activeTab ? (envelope.prd_id ?? tabPrdId) : null
          // change_tickets_template's own target: the thread's standalone set
          // outranks the tab PRD's tickets, because a thread that generated a
          // set has that set on screen — its tickets are what "the tickets"
          // means here. Resolved HERE (ChatScreen-local tab state), passed
          // through the primitive's ctx.
          const ticketsTarget =
            !docFile && activeTab
              ? activeTab.ticketSetId != null
                ? { ticketSetId: activeTab.ticketSetId } as const
                : targetPrdId != null ? { prdId: targetPrdId } as const : null
              : null
          const result = dispatchChatIntent(
            envelope,
            {
              hasEditTarget: targetPrdId != null,
              editTargetPrdId: targetPrdId,
              ticketsTarget,
            },
            {
              onGenerateTickets: (env) => {
                if (docFile) {
                  setAttachments([])
                  importPrdCommandFlow(docFile, {
                    openTickets: true, seedQuery: trimmed,
                    artifactTemplateId: env.artifact_template_id,
                  })
                  settlePendingSend()
                  return
                }
                if (activeTab?.prd) {
                  setContent({ prd: activeTab.prd, prdMeta: activeTab.briefMeta })
                  openContentPanel("tickets")
                  settlePendingSend()
                  return
                }
                // No PRD on this tab → a STANDALONE ticket set. The runner owns
                // the scope patch, the generating flag and the panel open; this
                // branch only decides that a set is what the user asked for.
                if (ticketSetInFlightGuard()) return
                ticketSetCommandFlow(
                  trimmed, env.task?.trim() || trimmed, env.artifact_template_id,
                )
                settlePendingSend()
              },
              onEditPrd: (instruction, prdId) => {
                void prdChatEditFlow(instruction, activeTab!.id, prdId!)
                settlePendingSend()
              },
              onOpenArtifact: (open) => {
                // OPEN, never generate. The two verbs are told apart in exactly
                // one place (backend app/chat_intent.py's OPEN-vs-GENERATE
                // rule) and this is the whole of the client's half: it can open
                // a document, ask which one, or say there isn't one — it has no
                // path into any generation flow, so a misfire here can never
                // cost the user an unwanted PRD.
                openArtifactFlow(trimmed, open)
                settlePendingSend()
              },
              onGeneratePrd: (env) => {
                if (docFile) {
                  setAttachments([])
                  importPrdCommandFlow(docFile, {
                    openTickets: false, seedQuery: trimmed,
                    artifactTemplateId: env.artifact_template_id,
                  })
                  settlePendingSend()
                  return
                }
                prdCommandFlow(trimmed, env.task, env.artifact_template_id)
                settlePendingSend()
              },
              onChangeTemplate: (env, prdId) => {
                // The in-place format switch. dispatchChatIntent's own guard
                // (ctx.hasEditTarget && env.artifact_template_id) already
                // ensures prdId is non-null and a format id is present before
                // this executor ever runs.
                void prdChangeTemplateFlow(
                  trimmed, activeTab!.id, prdId!,
                  env.artifact_template_id!, env.artifact_template_name,
                )
                settlePendingSend()
              },
              onChangeTicketsTemplate: (env, target) => {
                // The tickets' in-place format switch. The primitive's guard
                // (ctx.ticketsTarget && env.artifact_template_id) already
                // ensures both are present before this executor runs; the
                // set-over-PRD preference was resolved into `ticketsTarget`
                // above.
                void ticketsChangeTemplateFlow(
                  trimmed, activeTab!.id, target,
                  env.artifact_template_id!, env.artifact_template_name,
                )
                settlePendingSend()
              },
              onListArtifacts: (env) => {
                // "What are my PRDs?" — the rows rode the envelope; render
                // them as clickable cards (empty included: "none yet" is the
                // listing's own honest answer, not a fall-through).
                listArtifactsFlow(trimmed, env)
                settlePendingSend()
              },
              onCreateArtifact: (env) => {
                // "Draft a leadership update on the Q3 reliability work" —
                // write a team document and open it in THIS chat's right panel.
                //
                // SEEDS A REAL TURN, like every other command flow here. The
                // first cut just fired the request and returned: the composer
                // cleared, the optimistic bubble vanished, and the thread
                // showed nothing — no question, no acknowledgement, nothing
                // persisted. From the user's side that is indistinguishable
                // from the message being swallowed, which is the very
                // complaint this flow exists to fix.
                documentCommandFlow(trimmed, env)
                settlePendingSend()
              },
              onShareToSlack: (env) => {
                // "Share this PRD on my slack channel and ask the team for
                // feedback." PREVIEWS ONLY — the flow resolves the document
                // and the channel and puts the message on screen; the post
                // waits for the user's confirmation in the card.
                void shareToSlackFlow(trimmed, activeTab!.id, env)
                settlePendingSend()
              },
              onAssignTickets: (instruction, prdId) => {
                // Change who OWNS tickets. dispatchChatIntent's own guard
                // (ctx.hasEditTarget && envelope.instruction) already ensures
                // prdId is non-null and an instruction is present before this
                // executor ever runs.
                void assignTicketsFlow(trimmed, activeTab!.id, prdId!, instruction)
                settlePendingSend()
              },
              // No resolvable edit/format/assign target/instruction, no open
              // lookup, or answer/low-confidence/unknown/generate_prototype →
              // nothing to do here; ChatScreen's own grounded-ask code below
              // already runs unconditionally whenever `result.handled` is
              // false.
              onAnswer: () => {},
            },
          )
          if (result.handled) return
        }
      }
      // Attached file content is folded into the ask as context. Text
      // attachments inline directly; document attachments (.pdf/.pptx/.docx/.doc)
      // are parsed to markdown server-side (POST /v1/ask/extract-file) so a deck
      // attached to a plain question reaches the agent too — they used to be
      // silently dropped here, which read as "no document was attached" replies.
      // `displayQuery` is what the thread shows (the user's ask, plus a chip per
      // attachment — never the raw document dump). `sendQuery` is what the ask
      // agent receives: the same text with the parsed attachment content folded
      // in. Keeping them separate means the backend is unaffected while the UI
      // stays clean, the way Claude's chat renders it.
      const displayQuery = trimmed
      // (The empty-send no-op is checked at the top of this function, before the
      // pending-send placeholder is rendered.)
      // Early cheap guard: if the ACTIVE tab already has an ask in flight, bail
      // before doing any work. (Authoritative per-tab guard happens once
      // targetTabId is resolved below — needed for the no-active-tab case where
      // openTab creates the target.)
      if (activeTabId != null && askingTabsRef.current.has(activeTabId)) {
        settlePendingSend()
        return
      }
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      // Capture the target tab ID up-front so async callbacks always write to
      // the right tab, even if the user switches tabs while the request is in-flight.
      let targetTabId: string
      const hasAttachments = attachments.length > 0
      // OPTIMISTIC RENDER FIRST (the reported latency bug): the thread turn — the
      // user's message + a chip per attachment — must appear on THIS commit,
      // BEFORE the extractFile network call, so the composer never clears into a
      // void. The chips render from NAMES here; each attachment's extracted
      // content is folded onto the turn AFTER extraction resolves (below). The
      // folded-in document text still rides `sendQuery` to the backend, never the
      // thread bubble.
      const newTurn: ThreadTurn = {
        id,
        query: displayQuery,
        ...(hasAttachments ? { attachments: attachments.map((a) => ({ name: a.name })) } : {}),
      }
      // The tab title/handle falls back to the first attachment's name when the
      // ask itself is empty, so a doc-only send still reads sensibly in the tab.
      const handle = displayQuery || attachments[0]?.name || "New chat"
      // Remember where we started so an extraction failure can roll the optimistic
      // turn back cleanly: remove a freshly-spawned tab (restoring the prior
      // surface) or drop just this turn + undo a New-chat rename on an existing one.
      const prevActiveTabId = activeTabId
      const spawnedNewTab = !activeTabId || activeTabId === BRIEF_TAB_ID
      const prevTitle = spawnedNewTab
        ? null
        : tabsRef.current.find((t) => t.id === activeTabId)?.title ?? null
      // No active tab, OR the active "tab" is the synthetic, thread-less brief
      // tab → spawn a FRESH chat tab seeded with the query. A chat started from
      // the Top Insights brief must never thread inline into it (the brief tab carries
      // no `tabs` entry, so appending would silently no-op anyway).
      if (spawnedNewTab) {
        const title = handle.length > 40 ? `${handle.slice(0, 37)}…` : handle
        targetTabId = openTab(title, [newTurn])
      } else {
        targetTabId = activeTabId
        const newTitle = handle.length > 40 ? `${handle.slice(0, 37)}…` : handle
        setTabs((prev) => prev.map((t) => {
          if (t.id !== targetTabId) return t
          // First message in a placeholder "New chat" tab → give it the real
          // title from the query (rename in place; do NOT spawn a second tab).
          const title = t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? newTitle : t.title
          return { ...t, title, thread: [...t.thread, newTurn] }
        }))
      }
      // The real turn is now on the tab, so the placeholder has been handed off.
      // Same tick as the openTab/setTabs above → React batches both into ONE
      // commit, so the swap from placeholder to turn never flickers.
      settlePendingSend()
      // Hand the placeholder's clock over with it, so the wait ladder measures
      // one continuous wait rather than resetting to rung 0 at the handoff.
      askStartRef.current.set(id, askStartedAt)
      // A fresh ask on this tab clears any leftover Stop flag from a prior ask so
      // the new one is never treated as pre-stopped.
      stoppedTabsRef.current.delete(targetTabId)

      // Now — AFTER the turn is on screen — parse the attachments. Mark the tab
      // busy FIRST so the just-rendered turn shows the thinking skeleton (not the
      // terminal "no response" fallback, which needs live busy state) while the
      // extract runs. runTabAsk re-adds this (addToSet is idempotent) and owns the
      // eventual clear on the ask's completion.
      let sendQuery = displayQuery
      // Extracted attachment texts + the ORIGINAL file's storage key, persisted
      // with the turn (survives reload; text read back by conversationPrdDocs for
      // a later "generate a PRD"; key lets the chip render/download the real file).
      let persistedAttachments: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | undefined
      if (hasAttachments) {
        setBusyTabs((prev) => addToSet(prev, targetTabId))
        const pending = attachments
        let ctx: string
        try {
          // Per attachment, in parallel: (1) extract its text ONCE — plain-text
          // inlines its content, documents (.pdf/.pptx/.docx/.doc) are parsed to
          // markdown server-side; (2) upload the ORIGINAL file to storage so the
          // chip can render/download the real document after a reload. The upload
          // is best-effort (a failure leaves the text-only chip, never blocks the
          // send). Order is preserved via the resolved array.
          // The per-attachment extract (client-text | early-extracted | server
          // markdown) + best-effort upload → `AttachmentRef[]` is defined ONCE in
          // `resolveAttachmentRefs` (shared with the project composers via
          // `buildSendCommand`). `earlyExtracted` (done above so the planner could
          // see the text) is passed through so a document is never parsed twice.
          const extracted = await resolveAttachmentRefs(pending, { preExtracted: earlyExtracted })
          // Clamp the TOTAL context so question + attachments stay under the
          // ask endpoint's 120k question cap even with several attachments.
          ctx = extracted
            .map((e) => `--- ${e.name} ---\n${e.content}`)
            .join("\n\n")
            .slice(0, 100000)
          // Backfill the extracted content + stored file key onto the optimistic
          // turn so its card opens a viewer / downloads the original.
          const withContent = extracted.map((e) => ({ name: e.name, content: e.content, key: e.key, mime: e.mime, size: e.size }))
          persistedAttachments = withContent
          setTabs((prev) => prev.map((t) => t.id === targetTabId
            ? { ...t, thread: t.thread.map((tn) => tn.id === id ? { ...tn, attachments: withContent } : tn) }
            : t))
          sendQuery = `${sendQuery}\n\n[Attached files]\n${ctx}`
          setAttachments([]) // clear after successful extraction only
        } catch (e) {
          // Extraction failed: roll the optimistic turn back so no ghost
          // "thinking" bubble is stranded, but KEEP the attachments so the user
          // can retry or remove the bad one — a silent drop is exactly the failure
          // mode this path exists to fix.
          setBusyTabs((prev) => removeFromSet(prev, targetTabId))
          if (spawnedNewTab) {
            // This tab existed only for the failed send — remove it and restore
            // the prior surface rather than leaving an empty stray tab behind.
            setTabs((prev) => prev.filter((t) => t.id !== targetTabId))
            setActiveTabId(prevActiveTabId)
          } else {
            // Appended to an existing tab — drop just this turn and undo any
            // New-chat rename so the tab looks exactly as it did before the send.
            setTabs((prev) => prev.map((t) => t.id === targetTabId
              ? { ...t, title: prevTitle ?? t.title, thread: t.thread.filter((tn) => tn.id !== id) }
              : t))
          }
          showToast("Couldn't read attachment", (e instanceof Error ? e.message : String(e)).slice(0, 200))
          return
        }
      }
      pushPendingConversation(id, displayQuery, targetTabId, persistedAttachments)
      setActiveConv(0)
      // (Suggestions were cleared at the top of this function, before any await
      // or early return — deliberately NOT here. See the note there.)
      // The conversation id resolved inside `ask` below, captured so the
      // post-answer suggestion fetch can reuse it without a second lookup.
      let askConvId: number | null = null
      // runTabAsk holds the AUTHORITATIVE per-tab in-flight guard + busy marking.
      // It returns false (running nothing) if this tab already has an ask in
      // flight; otherwise it runs askApi.ask CONCURRENTLY with other tabs' asks
      // and routes the reply/error to the captured targetTabId. The guard, busy
      // toggling, and cleanup (even if the tab is closed mid-flight) all live in
      // the helper so the concurrency contract is unit-tested in one place.
      await runTabAsk({
        targetTabId,
        asking: askingTabsRef.current,
        setBusy: setBusyTabs,
        // Fire-and-forget + poll: POST returns an ask_id, the answer keeps
        // generating server-side, and the active ask_id is persisted per tab
        // (jobResume) so a backgrounded/remounted tab re-attaches via the mount
        // resume effect instead of re-asking.
        ask: async () => {
          // The conversation id this ask belongs to. On a FOLLOW-UP the tab
          // already carries it and this resolves without a round trip; on the
          // tab's FIRST message the row is still being created —
          // pushPendingConversation fires the create and deliberately does not
          // await it — so reading `dbConvId` synchronously here would yield
          // null. That is how a first-message HTML report got captured with
          // conversation_id NULL and the Reports panel then said "No reports in
          // this chat" (staging P1, 2026-07-30): the id is fixed at REQUEST
          // time and nothing backfills it afterwards.
          //
          // `ensureConversation` shares the very same in-flight create the turn
          // persistence uses (create-once per tab), so awaiting it costs at most
          // the remainder of one already-issued request and never mints a second
          // conversation. It resolves null on failure, so a create that fails
          // still lets the ask through — exactly the previous behaviour.
          const convId =
            tabsRef.current.find((t) => t.id === targetTabId)?.dbConvId ??
            (await persistence.ensureConversation(targetTabId, {
              turnId: id,
              title: displayQuery.length > 52
                ? `${displayQuery.slice(0, 49)}…`
                : displayQuery,
              query: displayQuery,
            }))
          askConvId = convId ?? null
          // Resolved AFTER the await — tabsRef, not the closure — so a
          // conversation created (or a PRD that finished generating) AFTER the
          // tab opened is still picked up. `sendQuery` carries any attached-
          // document content; `isStopped` lets the user stop the ask.
          const targetTab = tabsRef.current.find((t) => t.id === targetTabId)
          return runAskGeneration(sendQuery, activeCompany, targetTabId, {
            isCancelled: () => !mountedRef.current,
            isStopped: () => stoppedTabsRef.current.has(targetTabId),
            // Live token stream: the accumulating answer markdown renders in
            // place of the thinking skeleton as the model writes it. Display
            // only — onResult's authoritative reply replaces it.
            onPartial: (text) => {
              setTabs((prev) => prev.map((t) =>
                t.id !== targetTabId ? t : {
                  ...t, thread: t.thread.map((turn) =>
                    turn.id === id && !turn.reply && !turn.stopped
                      // A delta arriving after a drop means the preview came
                      // back — clear the note rather than leave it contradicting
                      // text that is visibly moving again.
                      ? { ...turn, partial: text, streamDropped: false }
                      : turn),
                }
              ))
            },
            // The live preview died mid-answer while the poll carries on. Purely
            // a display downgrade ("Finishing the answer" + a note) — the poll
            // is still authoritative and a stream failure is never an error.
            onStreamDrop: () => {
              setTabs((prev) => prev.map((t) =>
                t.id !== targetTabId ? t : {
                  ...t, thread: t.thread.map((turn) =>
                    turn.id === id && !turn.reply && !turn.stopped ? { ...turn, streamDropped: true } : turn),
                }
              ))
            },
            // Replay this tab's conversation so the model sees the prior turns
            // (history) on EVERY follow-up, not just PRD-tab chats — the backend
            // loads history by conversation_id, so without this each ask is
            // context-free and a follow-up like "get all in to-do status" loses
            // the thread it was answering. It is ALSO what attaches a captured
            // HTML report to this chat room (app/report_capture.py).
            ...(convId != null ? { conversation_id: convId } : {}),
            // PRD-tab chat: also send the PRD id so the answer is grounded on the
            // open PRD + its insight/evidence/tickets/prototype.
            ...(targetTab?.prdId != null ? { prd_id: targetTab.prdId } : {}),
            // Standalone-artifact grounding: ONE primary artifact per tab,
            // PRD first (its context already carries evidence + tickets),
            // then an open evidence report, then a standalone ticket set.
            ...(targetTab?.prdId == null && targetTab?.evidenceId != null
              ? { evidence_id: targetTab.evidenceId }
              : {}),
            ...(targetTab?.prdId == null && targetTab?.evidenceId == null
              && targetTab?.ticketSetId != null
              ? { ticket_set_id: targetTab.ticketSetId }
              : {}),
          })
        },
        onResult: (tabId, res) => {
          // If the answer already streamed in live, replaying the simulated
          // typewriter over the (identical) final text would type the whole
          // reply out twice — mark it as already animated.
          const streamedTurn = tabsRef.current
            .find((t) => t.id === tabId)?.thread.find((turn) => turn.id === id)
          if (streamedTurn?.partial) animatedTurnIds.current.add(id)
          askStartRef.current.delete(id)
          resumedTurnsRef.current.delete(id)
          setTabs((prev) => prev.map((t) =>
            t.id !== tabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === id
                ? { ...turn, reply: res, partial: undefined, streamDropped: undefined, timedOut: undefined }
                : turn)
            }
          ))
          const persisted = finalizeConversationTurn(id, { reply: res }, tabId)
          // Suggestions are fetched HERE — after the answer is on screen — and
          // deliberately not awaited by the turn: it is already complete, so a
          // slow or failed request degrades to the ordinary empty state. Only
          // the error path is handled, because there is nothing to report; a
          // rejection and an empty list mean the same thing to the user.
          //
          // It DOES wait on `persisted`, though. The backend reads the thread
          // from the database, so firing before this turn's assistant row lands
          // would ask "what comes next?" about a conversation missing the very
          // exchange it should continue — and on a first message the thread
          // would look empty and abstain every time.
          //
          // The whole block is wrapped: `onResult` runs inside runTabAsk, which
          // turns anything thrown here into the TURN's error path — so a
          // synchronous fault in this optional extra would surface as "Ask
          // failed" over an answer that actually succeeded. Nothing about a
          // suggestion strip is worth that, and it costs one try/catch to make
          // it structurally impossible.
          if (askConvId != null) {
            const convId = askConvId
            const prdId = tabsRef.current.find((t) => t.id === tabId)?.prdId ?? null
            try {
              void Promise.resolve(persisted)
                .then(() => chatSuggestionsApi.next(convId, { prdId }))
                .then(({ suggestions }) => {
                  // Late arrival guards: the screen may have unmounted, the tab
                  // closed, or the user already sent the NEXT message (which
                  // cleared the strip and left an ask in flight). In that last
                  // case these chips belong to a superseded turn — drop them.
                  if (!mountedRef.current || !suggestions?.length) return
                  if (!tabsRef.current.some((t) => t.id === tabId)) return
                  if (askingTabsRef.current.has(tabId)) return
                  setSuggestionsByTab((prev) => ({ ...prev, [tabId]: suggestions }))
                })
                .catch(() => { /* silence is the designed fallback */ })
            } catch { /* same fallback, for a synchronous throw */ }
          }
        },
        onError: (tabId, e) => {
          // Poll cancelled because the user left the chat screen mid-flight: the
          // ask_id is still persisted, so the mount-time resume effect will
          // re-attach and populate on return. Not a failure — no error UI/toast.
          if (e instanceof AskCancelledError) return
          // User hit Stop: the stopped turn is already rendered by handleStopAsk.
          // Not a failure — no error bubble/toast.
          if (e instanceof AskStoppedError) return
          askStartRef.current.delete(id)
          resumedTurnsRef.current.delete(id)
          // The 12-minute client budget expired while the job was still
          // generating. The ask_id is deliberately still persisted, so this is
          // NOT a failure: the turn says the answer is still running and a
          // reload will pick it up, which the resume effect then does.
          if (e instanceof AskTimeoutError) {
            setTabs((prev) => prev.map((t) =>
              t.id !== tabId ? t : {
                ...t, thread: t.thread.map((turn) => turn.id === id
                  ? { ...turn, timedOut: true, partial: undefined, streamDropped: undefined }
                  : turn),
              }
            ))
            return
          }
          // THE AI PROVIDER REFUSED THE REQUEST — say so, loudly. The error
          // bubble carries the sentence too, but a bubble in one tab's thread
          // is easy to scroll past, and this is a whole-account condition:
          // every other tab and every other surface is failing the same way
          // for the same reason. Observed 2026-08-16 with an exhausted
          // Anthropic balance — the product degraded correctly everywhere and
          // announced it nowhere.
          //
          // `persist` so it does NOT auto-dismiss: an out-of-credits account
          // needs an admin to act, and a toast that vanishes in four seconds
          // is indistinguishable from never having been shown.
          const providerNotice =
            e && typeof e === "object" && "providerNotice" in e
              ? (e as { providerNotice?: ProviderNotice }).providerNotice
              : undefined
          if (providerNotice) {
            showToast(
              providerNoticeTitle(providerNotice),
              providerNotice.message,
              undefined,
              { persist: true },
            )
          }
          const detail = e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
            ? (e.body as { detail: unknown }).detail
            : null
          const detailStr =
            typeof detail === "string"
              ? detail
              : Array.isArray(detail)
                ? detail
                  .map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x)))
                  .join(" · ")
                : null
          const msg =
            e instanceof ApiError
              ? detailStr || e.message
              : e instanceof Error
                ? e.message
                : "Something went wrong"
          setTabs((prev) => prev.map((t) =>
            t.id !== tabId ? t : {
              // Drop any streamed partial too: a half-answer above an error
              // bubble would read as the reply having (partly) succeeded.
              ...t, thread: t.thread.map((turn) => turn.id === id
                ? { ...turn, error: msg, partial: undefined, streamDropped: undefined }
                : turn)
            }
          ))
          // `msg` is kept on the turn and in the persisted conversation row as
          // the RECORD of what failed. It is not what the user reads: the failed
          // turn renders fixed copy, and so does this toast. A backend detail
          // string means nothing to the person who asked the question, and the
          // 404 the tenant gate raises must not tell a foreign tenant that the
          // row it asked for exists somewhere.
          finalizeConversationTurn(id, { error: msg }, tabId)
          showToast("Ask failed", WAIT_FAILED_TITLE)
        },
      })
    },
    [activeCompany, activeTabId, attachments, assignTicketsFlow, clearSuggestions, finalizeConversationTurn, importPrdCommandFlow, markClarifyResolved, openArtifactFlow, openContentPanel, openTab, prdChatEditFlow, prdCommandFlow, pushPendingConversation, runClarifiedGeneration, setContent, showToast, ticketsChangeTemplateFlow, listArtifactsFlow],
  )

  // ── Stop an in-flight ask ─────────────────────────────────────────────────
  // The composer's Send button becomes a Stop button while the active tab's ask
  // is generating. Stopping is deliberate (unlike a background unmount): it
  // reclaims the composer AT ONCE, marks the in-flight turn `stopped`, and asks
  // the backend to cancel so the worker aborts before its next LLM step and any
  // late answer is discarded server-side.
  const handleStopAsk = useCallback(() => {
    const tabId = activeTabId
    if (!tabId) return
    // 1) Signal the running poller to bail — it clears the persisted ask_id (so a
    //    remount won't resume) and rejects with AskStoppedError, which onError
    //    swallows. Checked on the poll's next tick.
    stoppedTabsRef.current.add(tabId)
    // 2) Best-effort backend cancel: the worker polls the job status between LLM
    //    steps and aborts before the expensive answer call when it lands early.
    const pending = getPendingAsk(activeCompany, tabId)
    if (pending) {
      const askId = Number(pending.id)
      if (Number.isFinite(askId)) void askApi.cancel(askId).catch(() => {})
    }
    // 3) Reclaim the composer immediately rather than waiting for the poll's next
    //    tick (runTabAsk's finally also clears these — the double-clear is safe).
    askingTabsRef.current.delete(tabId)
    setBusyTabs((prev) => removeFromSet(prev, tabId))
    // 4) Replace the in-flight turn's thinking skeleton with a muted stopped note.
    //    The in-flight turn is the last one still awaiting a reply.
    setTabs((prev) => prev.map((t) => {
      if (t.id !== tabId) return t
      let idx = -1
      for (let i = t.thread.length - 1; i >= 0; i--) {
        const turn = t.thread[i]
        if (!turn.reply && !turn.error && !turn.stopped) { idx = i; break }
      }
      if (idx === -1) return t
      return { ...t, thread: t.thread.map((turn, i) => i === idx ? { ...turn, stopped: true, partial: turn.partial, streamDropped: undefined } : turn) }
    }))
  }, [activeTabId, activeCompany, setBusyTabs])

  // "Ask again" on a stopped / timed-out / failed turn — the surface used to be
  // a dead end at all three.
  //
  // Attachments are NOT re-sent: their bytes left component state on the
  // original send, and quietly re-asking the same words WITHOUT the files the
  // user attached is a different question. So a turn that carried files hands
  // its text back to the composer instead, which is also what the failure copy
  // ("try it with fewer files attached") tells the reader to do.
  const handleAskAgain = useCallback((turn: ThreadTurn) => {
    const q = turn.query.trim()
    if (!q) return
    if (turn.attachments?.length) {
      setDraft(turn.query)
      composerRef.current?.focus()
      return
    }
    void submitAsk(q)
  }, [submitAsk])

  // ── Brief → new chat tab hand-off ─────────────────────────────────────────
  // A question typed on the top-insights surface must open its OWN chat tab, not
  // thread inline into the brief. BriefChat sets pendingChatHandoff; we consume
  // it once here by running it through submitAsk. With the brief tab active (the
  // only place this fires), submitAsk spawns a fresh tab seeded with the query —
  // so every chat started from the brief lands in a new tab.
  useEffect(() => {
    if (!pendingChatHandoff) return
    const { query } = pendingChatHandoff
    setPendingChatHandoff(null)
    void submitAsk(query)
  }, [pendingChatHandoff, setPendingChatHandoff, submitAsk])

  // ── PRD → new chat tab hand-off ───────────────────────────────────────────
  // A "view/generate PRD" from another surface (brief cards, brief composer,
  // backlog) fills pendingPrdTab via openPrdTab and routes to `/`. Consume it
  // once — openPrdInTab spawns the chat tab, drives the source, and flags the
  // content panel to open over it.
  useEffect(() => {
    if (!pendingPrdTab) return
    const req = pendingPrdTab
    setPendingPrdTab(null)
    const tabId = openPrdInTab(req)
    // A command-seeded turn (already rendered in the tab's thread by
    // openPrdInTab) also lands in the conversations rail + Supabase, so the
    // exchange survives a reload like any other chat turn.
    if (req.seedQuery) {
      const turnId = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      pushPendingConversation(turnId, req.seedQuery, tabId)
      finalizeConversationTurn(turnId, { reply: commandAckReply(req) }, tabId)
    }
  }, [pendingPrdTab, setPendingPrdTab, openPrdInTab, pushPendingConversation, finalizeConversationTurn])

  // Slide the content panel open on the commit AFTER openPrdInTab flags it. The
  // deferral matters when the PRD was opened from another surface: openPrdTab
  // routes to `/`, and NavigationContext closes the panel on that route change —
  // opening it here a commit later (route now settled) survives that close.
  useEffect(() => {
    if (!prdPanelPending) return
    const landOn = prdPanelPending
    setPrdPanelPending(null)
    openContentPanel(landOn)
  }, [prdPanelPending, openContentPanel])

  // The content panel is a single global overlay, but it must FOLLOW the active
  // tab: a PRD-bound tab shows its PRD on the right; the brief tab or a plain chat
  // shows nothing. Because the panel is global, this has to be reconciled on every
  // genuine tab switch. On switching to…
  //   • a PRD-bound tab (a PRD already cached/generating, or one in the DB) → open
  //     it (handleOpenPrd syncs the cached PRD or DB-loads by id). This is what
  //     makes REFOCUSING a PRD tab bring its panel back, instead of leaving it
  //     closed after you'd visited another tab.
  //   • an evidence tab (Top Insights → View Evidence) that hasn't grown a PRD of
  //     its own → reopen it on the Evidence tab, scoped to its finding.
  //   • the brief tab, or a plain (non-PRD) chat → close any lingering panel so it
  //     never hangs over the wrong surface.
  // Gated on an actual switch (prevTabForPanelRef) so a manual panel-close while
  // staying on a tab isn't immediately undone, and so the brief's own inline
  // actions (Tickets / Evidence / multi-agent — which open the panel WITHOUT a
  // switch) are untouched. `prdPanelPending` (set by openPrdInTab a commit before
  // it opens the panel) suppresses the reconcile during that hand-off.
  const autoRestoredTabsRef = useRef<Set<string>>(new Set())
  const prevTabForPanelRef = useRef(activeTabId)

  // Tabs whose Reports panel has already been opened for them ON THIS VISIT.
  // Shared by the Artifacts hand-off and the auto-open further down, so that
  // between them they open a thread's reports exactly once — and a panel the user
  // then CLOSES stays closed instead of being reopened by the other path.
  //
  // The claim is retired when you LEAVE the tab (below), which is what makes
  // coming back to a report thread bring its panel back. That mirrors the PRD
  // branch of the reconcile, which re-opens a PRD tab's document on every
  // refocus: a thread's artifact is what the thread is about, so returning to it
  // should show it. Scoping the claim to the visit is the whole difference
  // between "a manual close sticks while you're here" and "a manual close hides
  // this thread's report forever".
  const reportsAutoOpenedRef = useRef<Set<string>>(new Set())

  // A chat re-opened from history whose conversation carries a PRD: open its
  // panel once the resumed tab is actually active. Deferred through state rather
  // than called inline because checkResume sets the active tab in the same pass —
  // handleOpenPrd reads `activeTabId`, which has not updated yet at that point.
  useEffect(() => {
    if (!resumePanelTabId || activeTabId !== resumePanelTabId) return
    setResumePanelTabId(null)
    autoRestoredTabsRef.current.add(resumePanelTabId)
    void handleOpenPrd()
  }, [resumePanelTabId, activeTabId, handleOpenPrd])
  useEffect(() => {
    const switchedTab = prevTabForPanelRef.current !== activeTabId
    // Leaving a tab retires its Reports auto-open claim, so refocusing it later
    // opens its report again (see reportsAutoOpenedRef). Done here, before the
    // early returns below, because it must happen on EVERY switch — including
    // the ones this reconcile then declines to act on.
    if (switchedTab && prevTabForPanelRef.current) {
      reportsAutoOpenedRef.current.delete(prevTabForPanelRef.current)
      // Same claim, same retirement, for a thread whose artifact is a ticket
      // set — but only once the run is DONE. Retiring it mid-run would let the
      // resume probe put a second reader on a row the runner is already
      // polling, and the two publish different shapes (the runner streams the
      // job's stubs and batch progress; the probe reads the row).
      const left = tabsRef.current.find((t) => t.id === prevTabForPanelRef.current)
      if (!left?.ticketSetRunning) {
        ticketSetAutoOpenedRef.current.delete(prevTabForPanelRef.current)
      }
    }
    // Reconcile the SHARED ticket-set slot to the tab being switched TO, before
    // any of the early returns below — a set left on screen is wrong on every
    // switch, including the ones this reconcile then declines to act on.
    //
    // `content.ticketSet` is global but a set belongs to ONE thread, and it is
    // not merely what the Tickets tab renders: it is what makes that tab APPEAR
    // (ContentPanel's hidden gate). Left behind, thread A's set shows up on
    // thread B — and on a thread that has a PRD it displaces the PRD's own
    // tickets, which is what the glitch looked like from the outside.
    //
    // `handleOpenPrd` already clears it, but only on the open-an-EXISTING-PRD
    // path. A brand-new chat that GENERATES a PRD never goes through it, so the
    // stale set survived until some later effect happened to fire — hence a
    // wrong panel that "fixed itself after a moment".
    if (switchedTab) {
      const shown = ticketSetShownRef.current
      if (shown.id != null || shown.busy) {
        const arriving = tabsRef.current.find((t) => t.id === activeTabId)
        // The arriving tab's OWN set stays put (re-reading it would flicker),
        // and so does a run in flight there. Anything else belongs to a thread
        // the user just left.
        const ownsIt = !!arriving?.ticketSetRunning
          || (arriving?.ticketSetId != null && arriving.ticketSetId === shown.id)
        if (!ownsIt) {
          setContent({ ticketSet: null, ticketSetGenerating: false, ticketSetStandalone: false })
        }
      }
    }
    prevTabForPanelRef.current = activeTabId
    // `pendingReportFocus` suppresses the reconcile for the same reason
    // `prdPanelPending` does: a report opened from Artifacts resumes its thread,
    // which IS a tab switch, and that thread often carries no PRD — so this would
    // close the very panel the hand-off below is about to open.
    // `pendingTicketSetFocus` is the same hand-off, one artifact over.
    if (!switchedTab || prdPanelPending || pendingReportFocus || pendingTicketSetFocus) return
    // Brief tab or the tab-less landing → no PRD to show; drop any lingering panel.
    if (isBriefTab || !activeTabId) { if (contentPanelTab) closeContentPanel(); return }
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    // A PRD that landed IN THIS TAB, as opposed to one that merely exists in the
    // DB for the same insight. The distinction only matters for evidence tabs
    // (below), which must not be hijacked by a PRD they were never opened for.
    const tabOwnsPrd = !!tab?.prd || !!tab?.prdGenerating || tab?.prdId != null
    const ownsPrd = tabOwnsPrd || !!(chatInsightState?.hasPrd && chatInsightState.prdId != null)
    // An evidence tab (Top Insights → View Evidence) restores ITS evidence: the
    // finding is what the tab is about, so refocusing must neither close the panel
    // (it holds no PRD) nor jump to the insight's PRD. Once a PRD does land in the
    // tab — the panel's PRD tab resolves one on demand — that takes precedence.
    // After a reload `evidenceDetail` is gone; `prdMeta` alone still populates the
    // Evidence tab, via its read-only load-by-insight path.
    if (tab?.evidenceOnly && !tabOwnsPrd) {
      setContent({
        detail: tab.evidenceDetail ?? null,
        prd: null,
        prdMeta: tab.briefMeta,
        prdGenerating: false,
        prdPartialHtml: null,
      })
      openContentPanel("evidence")
      return
    }
    if (ownsPrd) {
      // Sync the global panel to THIS tab's PRD — ALWAYS, even if it already reads
      // "prd", because another PRD tab may have left ITS doc in the shared panel
      // (that was the "wrong PRD on refocus" bug). handleOpenPrd uses the cached
      // prd (instant) or DB-loads this tab's own id. Pre-claim the tab so the
      // reload-restore effect below doesn't ALSO fire handleOpenPrd this commit.
      autoRestoredTabsRef.current.add(activeTabId)
      void handleOpenPrd()
    } else if (contentPanelTab) {
      closeContentPanel()
    }
  }, [activeTabId, isBriefTab, contentPanelTab, prdPanelPending, pendingReportFocus, pendingTicketSetFocus, chatInsightState, handleOpenPrd, closeContentPanel, openContentPanel, setContent])

  // ── Report → its own thread hand-off ──────────────────────────────────────
  // Clicking a report in Artifacts writes the ordinary `sprntly_resume_conv`
  // hand-off and fills `pendingReportFocus`: checkResume spawns/refocuses that
  // conversation's tab, and this lands the panel on the report once that tab is
  // ACTUALLY active. Gated on the id matching so the panel can never open over a
  // different thread — if the resume fails, nothing opens rather than the wrong
  // thing. Declared after the reconcile above so that, on the commit where both
  // run, this open is the last word.
  useEffect(() => {
    if (!pendingReportFocus) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.dbConvId !== pendingReportFocus.conversationId) return
    setContent({ reportFocusId: pendingReportFocus.reportId, reportFocusStandalone: false })
    setPendingReportFocus(null)
    // Claim the tab: this IS its one auto-open, so closing the panel here must
    // not hand straight over to the auto-open effect below.
    reportsAutoOpenedRef.current.add(tab.id)
    openContentPanel("reports")
  }, [pendingReportFocus, setPendingReportFocus, activeTabId, tabs, setContent, openContentPanel])

  // ── A thread whose only artifact is reports opens on them ──────────────────
  // Opening a chat should SHOW what that chat produced. The PRD-bound paths
  // above already do that for PRDs; this covers the thread whose artifact is a
  // report, which otherwise came back with the panel shut and no sign the
  // document existed.
  //
  // Lands on the NEWEST report, not a list — the list is what "All reports"
  // is for, and a thread with several still has one that was just written.
  //
  // A PRD in the thread takes precedence and is left as the active tab — it's
  // the document the chat is about, and Reports is one click away in the tab bar
  // (that ordering is the explicit ask). Fires at most once per VISIT to a tab, so
  // a manual close is never undone while you are on it, and never over an
  // already-open panel — but coming back to the thread does show its report
  // again, the same as refocusing a PRD tab does.
  //
  // The list has to be THIS thread's, not merely loaded. `threadReports` is
  // already scoped to the active tab's conversation (see its memo above), and the
  // dbConvId check below states the other half: a tab with no conversation id yet
  // is a brand-new chat that cannot have reports, whatever the shared content
  // happens to be holding at this instant. Both together are what stopped a fresh
  // tab from sliding the previous thread's report in over an empty conversation.
  useEffect(() => {
    if (!activeTabId || isBriefTab || pendingReportFocus) return
    if (reportsAutoOpenedRef.current.has(activeTabId)) return
    // Only act on a KNOWN list — "not loaded yet" must not read as "no reports".
    if (content.threadReportsStatus !== "ready" || threadReports.length === 0) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.dbConvId == null) return
    if (content.threadReportsConversationId !== tab.dbConvId) return
    if (tab.prd || tab.prdGenerating || tab.prdId != null) return
    if (contentPanelTab) return // something is already open — don't hijack it
    reportsAutoOpenedRef.current.add(activeTabId)
    setContent({ reportFocusId: threadReports[0].id, reportFocusStandalone: false })
    openContentPanel("reports")
  }, [
    activeTabId, isBriefTab, pendingReportFocus, contentPanelTab,
    threadReports, content.threadReportsStatus, content.threadReportsConversationId,
    setContent, openContentPanel,
  ])

  // ── Ticket set → its own thread hand-off ──────────────────────────────────
  // Clicking a ticket set in Artifacts writes the ordinary `sprntly_resume_conv`
  // hand-off and fills `pendingTicketSetFocus`. Structurally the report hand-off
  // above: gated on the conversation id matching, so if the resume fails nothing
  // opens rather than the wrong thread's tickets.
  useEffect(() => {
    if (!pendingTicketSetFocus) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.dbConvId !== pendingTicketSetFocus.conversationId) return
    const setId = pendingTicketSetFocus.ticketSetId
    setPendingTicketSetFocus(null)
    // Claim the tab: this IS its one auto-open, so the resume probe below must
    // not immediately re-read the same row.
    ticketSetAutoOpenedRef.current.add(tab.id)
    setTabs((prev) => prev.map((t) => t.id === tab.id ? { ...t, ticketSetId: setId } : t))
    setContent({ ticketSetStandalone: false })
    openContentPanel("tickets")
    void loadTicketSet(setId, setContent)
  }, [pendingTicketSetFocus, setPendingTicketSetFocus, activeTabId, tabs, setContent, openContentPanel])

  // ── A thread that produced tickets opens on them ──────────────────────────
  // The explicit requirement, and the same principle as the two effects above:
  // opening a chat SHOWS what that chat produced. A PRD-bound thread opens its
  // PRD, a report thread its newest report — and a thread whose artifact is a
  // standalone ticket set came back with the panel shut and no sign the tickets
  // existed at all.
  //
  // Two states, both of which have to be right:
  //   • `generating` — the run is still going (started here, or in another
  //     browser tab). The panel lands on the LIVE progress, because
  //     `loadTicketSet` follows the durable row to a terminal state. It never
  //     leaves a "Writing tickets…" over a run that already finished: the very
  //     first read settles a row that is no longer generating.
  //   • `ready` — the panel lands on the tickets, never a blank Tickets tab.
  // A `failed` set is recorded on the tab (so the footer offers "Retry
  // tickets") but does NOT auto-open: reopening a chat should not greet you
  // with an error state you already dismissed once.
  //
  // Newest wins when a thread has several — a second ask creates a second set
  // by design, and the backend returns them newest-first.
  //
  // A PRD in the thread takes precedence and is left alone, matching the
  // reports effect: the PRD is the document the chat is about.
  useEffect(() => {
    if (!activeTabId || isBriefTab || pendingReportFocus || pendingTicketSetFocus) return
    if (ticketSetAutoOpenedRef.current.has(activeTabId)) return
    const tabId = activeTabId
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (!tab || tab.dbConvId == null) return
    if (tab.prd || tab.prdGenerating || tab.prdId != null) return
    const convId = tab.dbConvId
    // Claimed before the fetch, not after: this effect re-runs on every render
    // while its dependencies are unchanged-but-new, and an unclaimed probe
    // would issue the same request repeatedly.
    ticketSetAutoOpenedRef.current.add(tabId)
    void (async () => {
      try {
        const { ticketSetsApi } = await import("../../../lib/api")
        const sets = await ticketSetsApi.byConversation(convId)
          .then((r) => r.ticket_sets)
          .catch(() => [])
        if (!sets.length) return
        const newest = sets[0]
        setTabs((prev) => prev.map((t) => t.id === tabId
          ? { ...t, ticketSetId: newest.id, ticketSetStatus: newest.status }
          : t))
        // The user may have moved on during the round-trip — never open a panel
        // over a tab this set has nothing to do with.
        if (activeTabIdRef.current !== tabId) return
        if (newest.status === "failed") return
        if (contentPanelTabRef.current) return // something is already open
        setContent({ ticketSetStandalone: false })
        openContentPanel("tickets")
        void loadTicketSet(newest.id, setContent)
      } catch {
        // A resume PROBE must never throw. It runs on every chat open, its only
        // job is to surface an artifact that may not exist, and an unhandled
        // rejection here would take the thread down with it.
      }
    })()
  }, [
    activeTabId, isBriefTab, pendingReportFocus, pendingTicketSetFocus,
    tabs, setContent, openContentPanel,
  ])

  // ── Adopt a panel-resolved PRD onto the tab it belongs to ──────────────────
  // ContentPanel can resolve a PRD by itself — the Evidence footer's "Generate
  // PRD", and clicking the panel's PRD tab on an evidence tab — and it writes only
  // to the SHARED content, since it knows nothing about chat tabs. Left there, the
  // tab would still read as PRD-less: refocusing it would restore evidence (or
  // close the panel) while another tab's document sat in the shared panel — the
  // "wrong PRD on refocus" bug, from the other direction.
  //
  // So mirror it onto the active tab, gated on the PRD's own (briefId,
  // insightIndex) matching that tab's insight, so a document belonging to some
  // other tab is never adopted. Stamping `prdId` is also what lets a reload
  // recover the PRD by id.
  useEffect(() => {
    const prd = content.prd
    if (!prd || !activeTabId || isBriefTab) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.prd?.prd_id === prd.prd_id) return
    const meta = tab.briefMeta
    if (!meta || prd.briefId !== meta.briefId || prd.insightIndex !== meta.insightIndex) return
    setTabs((prev) => prev.map((t) => t.id === activeTabId
      ? { ...t, prd, prdId: prd.prd_id, prdGenerating: false }
      : t))
  }, [content.prd, activeTabId, isBriefTab])

  // ── Restore the PRD panel after a reload ───────────────────────────────────
  // Tabs persist across reloads (localStorage) but their cached `prd` does NOT —
  // it's stripped to keep storage small (see the slim persist above). So a reload
  // that lands back on a PRD-bound chat tab used to show the tab with the panel
  // CLOSED, forcing a manual "View PRD" click. Here we reopen it automatically:
  // once the brief prototype map resolves and confirms a PRD exists in the DB for
  // the ACTIVE tab's insight, open the panel and LOAD the saved PRD by id
  // (handleOpenPrd takes the DB-load branch — never a regeneration).
  //
  // Keyed on the active tab (not a captured mount tab): `activeCompany` resolves
  // asynchronously and the company-change effect re-seeds the active tab a commit
  // or two after mount, so "the tab we reloaded onto" isn't known at first render.
  //
  // We act ONLY on a positive signal — the map has resolved AND reports a DB PRD
  // for this insight — and mark the tab handled ONLY when we actually open it.
  // This matters because useBriefPrototypeMap starts `loading:false` with an empty
  // map and only flips to `loading:true` inside its own effect (a commit later):
  // an earlier design that gave up whenever `hasPrd` was false would latch onto
  // that empty pre-fetch window and never restore. Here a false/empty reading is
  // simply "not yet" — we wait for a later render. Guards keep the panel off the
  // wrong surface:
  //   • Never the brief tab, and never a plain (non-PRD) chat → a reload (or
  //     switch) onto a new chat leaves the panel closed.
  //   • Skips when the tab already holds/loads a PRD or a panel is already open
  //     (openPrdInTab / a manual open handled it) — and once opened, `tab.prd` is
  //     cached, so a manual panel-close is never undone.
  //   • Fires at most once per tab (autoRestoredTabsRef, shared with the switch
  //     reconcile above so the two never double-open the same tab in one commit).
  useEffect(() => {
    if (!activeTabId || isBriefTab) return
    if (autoRestoredTabsRef.current.has(activeTabId)) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    // Already loaded/loading, or a panel is already open → nothing to restore right
    // now (don't latch; these conditions are transient).
    if (!tab || tab.prd || tab.prdGenerating || contentPanelTab) return
    // This tab's OWN saved id restores immediately — no map needed. This is the
    // path that brings back a backlog PRD (no briefMeta) after a reload.
    if (tab.prdId != null) {
      autoRestoredTabsRef.current.add(activeTabId)
      void handleOpenPrd()
      return
    }
    // An evidence tab holds a FINDING, not a PRD — there is nothing for this
    // effect to restore. Its panel is opened by `prdPanelPending` on the open
    // itself and by the switch reconcile on refocus. Without this guard the map
    // branch below would drag the tab onto a PRD that merely exists for the same
    // insight, which is not the document it was opened for.
    if (tab.evidenceOnly) return
    // Otherwise it must be a brief-insight tab whose DB PRD the map confirms. A
    // not-yet-resolved map reads as hasPrd=false → treat as "wait", not "give up",
    // and re-check on the next render (the empty pre-fetch window latch bug).
    if (!tab.briefMeta) return
    if (!(chatInsightState?.hasPrd && chatInsightState.prdId != null)) return
    autoRestoredTabsRef.current.add(activeTabId)
    void handleOpenPrd()
  }, [activeTabId, isBriefTab, contentPanelTab, chatInsightState, handleOpenPrd])

  // ── Resume orphaned in-flight ASK jobs on (re)mount ───────────────────────
  // A chat Ask is fire-and-forget: POST returns an ask_id and the answer keeps
  // generating server-side. The pending USER turn lives in the persisted
  // tab.thread (so the question survives a remount), but the awaiting poll
  // closure + the in-memory asking/busy markers do NOT. If a pending ask_id was
  // persisted (jobResume), re-enter the visibility-aware poll against the
  // existing status endpoint — NOT re-POST — and re-show the "asking…" state.
  // Runs once per tab per mount.
  const resumedAskTabsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const tab of tabsRef.current) {
      if (resumedAskTabsRef.current.has(tab.id)) continue
      const pending = getPendingAsk(activeCompany, tab.id)
      if (!pending) continue
      const askId = Number(pending.id)
      if (!Number.isFinite(askId)) continue
      // Re-attach only when the last turn is still awaiting a reply (the
      // canonical "asking…" marker that survives in the persisted thread).
      const last = tab.thread[tab.thread.length - 1]
      if (!last || last.reply !== undefined || last.error !== undefined || last.stopped) continue
      if (askingTabsRef.current.has(tab.id)) continue
      resumedAskTabsRef.current.add(tab.id)
      const turnId = last.id
      const targetTabId = tab.id
      // Restore the optimistic asking/busy UX for this tab.
      askingTabsRef.current.add(targetTabId)
      setBusyTabs((prev) => addToSet(prev, targetTabId))
      stoppedTabsRef.current.delete(targetTabId)
      // This ask is being RE-ATTACHED by id, not re-POSTed — the one observable
      // fact behind "Picking up where this left off". The clock restarts from
      // this mount because the original start time is not available client-side,
      // which is exactly why the copy says the answer was already running rather
      // than claiming the elapsed number covers the whole job.
      resumedTurnsRef.current.add(turnId)
      askStartRef.current.set(turnId, Date.now())
      setResumeTick((n) => n + 1)
      void (async () => {
        try {
          const res = await resumeAskGeneration(
            askId,
            activeCompany,
            targetTabId,
            () => !mountedRef.current,
            () => stoppedTabsRef.current.has(targetTabId),
            // Re-attached mid-generation: the stream's replay frame catches the
            // preview up with everything already written, then live deltas.
            (text) => {
              setTabs((prev) => prev.map((t) =>
                t.id !== targetTabId ? t : {
                  ...t, thread: t.thread.map((turn) =>
                    turn.id === turnId && !turn.reply && !turn.stopped
                      ? { ...turn, partial: text, streamDropped: false }
                      : turn),
                }
              ))
            },
            () => {
              setTabs((prev) => prev.map((t) =>
                t.id !== targetTabId ? t : {
                  ...t, thread: t.thread.map((turn) =>
                    turn.id === turnId && !turn.reply && !turn.stopped ? { ...turn, streamDropped: true } : turn),
                }
              ))
            },
          )
          // Same reason as the onResult path above, on the route a user reaches
          // by reloading mid-answer: if this turn streamed, mark it animated
          // BEFORE the reply lands. `hasFreshReply` reads this set during
          // render, and without the mark the simulated typewriter would collapse
          // a long answer back to its first paragraph and re-reveal it at
          // ~600-800ms per paragraph — re-typing text already read.
          //
          // It has to be this durable, id-keyed marker and not a check on the
          // turn itself: the setTabs below clears `partial` in the SAME update
          // that sets `reply`, so by render time `turn.partial` is always
          // undefined and any predicate based on it would be dead code.
          const streamedTurn = tabsRef.current
            .find((t) => t.id === targetTabId)?.thread.find((turn) => turn.id === turnId)
          if (streamedTurn?.partial) animatedTurnIds.current.add(turnId)
          setTabs((prev) => prev.map((t) =>
            t.id !== targetTabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === turnId
                ? { ...turn, reply: res, partial: undefined, streamDropped: undefined, timedOut: undefined }
                : turn),
            }
          ))
          finalizeConversationTurn(turnId, { reply: res }, targetTabId)
        } catch (e) {
          // Unmounted again mid-resume: leave the marker so the NEXT mount
          // re-attaches. Don't write an error into the thread.
          if (e instanceof AskCancelledError) return
          // User stopped the resumed ask: the stopped turn is rendered by
          // handleStopAsk; not a failure, so no error bubble.
          if (e instanceof AskStoppedError) return
          // The resumed poll hit the 12-minute budget too — same honest state as
          // a first-run timeout, not a failure.
          if (e instanceof AskTimeoutError) {
            setTabs((prev) => prev.map((t) =>
              t.id !== targetTabId ? t : {
                ...t, thread: t.thread.map((turn) => turn.id === turnId
                  ? { ...turn, timedOut: true, partial: undefined, streamDropped: undefined }
                  : turn),
              }
            ))
            return
          }
          const msg = e instanceof Error ? e.message : "Something went wrong"
          setTabs((prev) => prev.map((t) =>
            t.id !== targetTabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === turnId
                ? { ...turn, error: msg, streamDropped: undefined }
                : turn),
            }
          ))
          finalizeConversationTurn(turnId, { error: msg }, targetTabId)
        } finally {
          askStartRef.current.delete(turnId)
          resumedTurnsRef.current.delete(turnId)
          askingTabsRef.current.delete(targetTabId)
          setBusyTabs((prev) => removeFromSet(prev, targetTabId))
        }
      })()
    }
  }, [activeCompany, finalizeConversationTurn])

  const handleComposerSubmit = () => {
    const q = draft.trim()
    // Backend rejects questions under 3 chars — match BriefChat's guard (the
    // send buttons are also disabled below 3, this covers Enter-to-send).
    if (q.length < DRAFT_MIN_CHARS) {
      if (q.length > 0) showToast("Question too short", "Use at least 3 characters.")
      return
    }
    // Cheap active-tab guard; submitAsk re-checks per the resolved target tab.
    //
    // This used to be a bare `return` — the single worst micro-interaction on
    // the surface. Enter while an ask was in flight did nothing at all: no
    // send, no message, the draft just sat there and the keystroke vanished.
    // The guard stays (one ask per tab); the silence does not.
    if (activeTabId != null && askingTabsRef.current.has(activeTabId)) {
      showComposerHint("busy")
      return
    }
    // A send is already mid-dispatch (its intent decision is still in flight).
    // The busy/asking markers aren't set until the ask itself starts, so without
    // this a second Enter during that window would double-send.
    if (pendingSend) return
    // A pinned skill is re-attached to the query as its slash trigger, so the
    // backend's deterministic fast-path sees exactly what typing it by hand
    // would have produced — the chip is a composer affordance, not a new
    // protocol. The trigger stays visible on the sent turn, which is what makes
    // the wait's skill chip verifiable from the thread itself.
    const sent = spliceSkill(pinnedSkill, q)
    // Sending ends the dictation that produced the question — and CANCELS it
    // rather than stopping it. A graceful stop still delivers the phrase the
    // engine was finalising, and the hook's transcript is cumulative, so that
    // trailing result would write the whole sent question back into the draft
    // this send is about to clear.
    if (voice.listening) voice.cancel()
    voiceBaseRef.current = ""
    setDraft("")
    setPinnedSkill(null)
    setPlusMenuOpen(false)
    void submitAsk(sent)
    const ta = composerRef.current
    if (ta) {
      // Clear the inline height so the textarea snaps back to its CSS resting
      // size (min-height + padding). A hardcoded value here is shorter than the
      // vertical padding and clips the placeholder after sending.
      ta.style.height = ""
    }
  }

  const filteredSkills = useMemo(() => {
    // One list now (the company's own uploads) — the built-in catalog it used
    // to be merged ahead of is gone. Server order is newest-first.
    return skills.filter(
      (s) =>
        slashFilter === "" ||
        s.trigger.toLowerCase().includes("/" + slashFilter) ||
        s.label.toLowerCase().includes(slashFilter) ||
        s.description.toLowerCase().includes(slashFilter),
    )
  }, [skills, slashFilter])
  const slashOpen = showSlash && filteredSkills.length > 0
  // Keep the highlight in range as the filtered list shrinks/grows.
  useEffect(() => {
    setSlashActive((i) => Math.min(i, Math.max(0, filteredSkills.length - 1)))
  }, [filteredSkills.length])

  // ⌘/ (Ctrl+/ on Windows) opens the skills palette from the keyboard.
  //
  // Both composers have advertised this shortcut in their footer for a while and
  // NOTHING was listening for it — there is no metaKey handler for "/" anywhere
  // in the app. The `+` menu now points at it too ("Browse skills ⌘/"), so the
  // hint had to become true rather than be repeated twice.
  const openSkillPalette = useCallback(() => {
    setSlashFromMenu(true)
    setSlashFilter("")
    setSlashActive(0)
    setShowSlash(true)
    setPlusMenuOpen(false)
    composerRef.current?.focus()
  }, [])

  const handleComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "/") {
      e.preventDefault()
      openSkillPalette()
      return
    }
    // When the slash palette is open, arrow keys / Enter / Tab drive it and Esc
    // dismisses it — the composer's own Enter-to-send yields to the picker.
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSlashActive((i) => (i + 1) % filteredSkills.length)
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSlashActive((i) => (i - 1 + filteredSkills.length) % filteredSkills.length)
        return
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault()
        handleSlashSelect(filteredSkills[slashActive] ?? filteredSkills[0])
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setShowSlash(false)
        setSlashFromMenu(false)
        return
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleComposerSubmit()
    }
  }

  const handleComposerInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setDraft(val)
    e.target.style.height = "auto"
    e.target.style.height = Math.min(e.target.scrollHeight, 240) + "px"
    // Slash command detection: show dropdown when text starts with /
    if (val.startsWith("/")) {
      setShowSlash(true)
      setSlashFromMenu(false)
      setSlashFilter(val.slice(1).toLowerCase())
      setSlashActive(0)
    } else if (!slashFromMenu) {
      // A palette opened by TYPING closes as soon as the draft stops being a
      // slash command. One opened from the `+` menu or ⌘/ stays put — a person
      // browsing skills over a half-written question would otherwise lose the
      // list on their next keystroke.
      setShowSlash(false)
    }
  }

  const handleSlashSelect = (skill: SkillInfo) => {
    setShowSlash(false)
    setSlashFromMenu(false)
    setSlashFilter("")
    // Pin the skill as a removable CHIP instead of pasting "/competitive-intel "
    // into the draft as raw text. The old behaviour handed the user a string
    // they had to preserve character-for-character or silently lose the skill,
    // sitting in the middle of a sentence they were about to write.
    setPinnedSkill({ id: skill.id, label: skill.label, trigger: skill.trigger })
    // Only a draft the palette itself put there is cleared — a question already
    // typed survives having a skill pinned onto it.
    setDraft((d) => (d.startsWith("/") ? "" : d))
    composerRef.current?.focus()
  }

  // The `+` menu: Attach a file / Browse skills. The slash palette used to be
  // reachable ONLY by typing "/" or already knowing ⌘/, so 78 skills were
  // invisible to anyone who never read the footer hint.
  const handlePlusMenuSelect = useCallback((index: number) => {
    setPlusMenuOpen(false)
    if (index === 0) {
      fileInputRef.current?.click()
      return
    }
    openSkillPalette()
  }, [openSkillPalette])

  // Esc stops the answer. The Stop button already sits in the composer and now
  // beside the wait itself, but the fastest way out of a run you regret is the
  // key everybody already presses to cancel things.
  //
  // It yields to anything that owns Esc more locally: the attachment viewer, the
  // slash palette and the `+` menu each close on Esc first.
  useEffect(() => {
    if (!busy) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      if (viewerAttachment || slashOpen || plusMenuOpen) return
      handleStopAsk()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [busy, viewerAttachment, slashOpen, plusMenuOpen, handleStopAsk])

  /** The skill a question DETERMINISTICALLY selected, or null.
   *
   *  Only a leading slash trigger counts: qa_agent's fast path treats it as an
   *  explicit selection, so naming it is a fact. What the ROUTER picked for a
   *  plain question is not knowable here — `_skill` is written into
   *  `ask_jobs.response` by `complete_ask_job`, so the payload is `{}` for the
   *  whole time the wait is on screen. Rather than guess, no chip is shown.
   *  (Surfacing the routed skill mid-run needs a `routed_skill` column; that is
   *  a separate backend change.) */
  const skillForQuery = useCallback((query: string): SkillInfo | null => {
    const first = query.trim().split(/\s+/)[0]
    if (!first || !first.startsWith("/")) return null
    const wanted = first.toLowerCase()
    return skills.find((s) => s.trigger.toLowerCase() === wanted) ?? null
  }, [skills])

  /** The composer's one status line. A dictation problem outranks the busy hint:
   *  the busy hint answers a key you just pressed and expires on its own, while
   *  a blocked microphone is a state you stay stuck in until you go and change a
   *  browser setting. */
  const composerHintNode: React.ReactNode = voice.error
    ? voice.error
    : composerHint === "busy"
      ? <>{BUSY_ENTER_HINT_LEAD}<b>Stop</b>{BUSY_ENTER_HINT_TAIL}</>
      : null

  /** ONE composer, rendered on the landing and in the thread dock. `home` is the
   *  only difference between the two calls — everything else is shared state, so
   *  the pair cannot drift again the way `.chat-home-composer` and
   *  `.bc-composer` did. */
  const renderComposer = (home: boolean) => (
    <ChatComposer
      home={home}
      busy={busy}
      draft={draft}
      pinnedSkill={pinnedSkill}
      attachments={attachments}
      hint={composerHintNode}
      menuOpen={plusMenuOpen}
      menuActiveIndex={plusMenuActive}
      slashMenu={slashOpen ? (
        <SlashSkillMenu
          skills={filteredSkills}
          activeIndex={slashActive}
          onSelect={handleSlashSelect}
          onHover={setSlashActive}
        />
      ) : null}
      composerRef={composerRef}
      fileInputRef={fileInputRef}
      onInput={handleComposerInput}
      onKeyDown={handleComposerKeyDown}
      onSend={handleComposerSubmit}
      onStop={handleStopAsk}
      onToggleMenu={() => { setPlusMenuActive(0); setPlusMenuOpen((o) => !o) }}
      onMenuActive={setPlusMenuActive}
      onMenuSelect={handlePlusMenuSelect}
      onCloseMenu={() => setPlusMenuOpen(false)}
      onRemoveAttachment={(i) => setAttachments((p) => p.filter((_, idx) => idx !== i))}
      onRemoveSkill={() => setPinnedSkill(null)}
      onFileSelect={handleFileSelect}
      voiceSupported={voice.supported}
      voiceListening={voice.listening}
      onToggleVoice={handleToggleVoice}
    />
  )

  const handleStarterChip = (text: string) => {
    void submitAsk(text)
  }

  const handleHomeCard = (c: ChatHomeCard) => {
    if (c.target === "ondemand" && c.prompt) {
      setPendingOndemandDraft(c.prompt)
      return
    }
    if (c.target === "ondemand") {
      goTo("chat")
      return
    }
    if (c.target === "brief" && c.prompt) {
      setAIBarValue(c.prompt)
      goTo("brief")
      expandAiPanel()
      return
    }
    goTo(c.target)
  }

  const startNewThread = useCallback(() => {
    // "+" behaves like a real new browser tab: it must create a VISIBLE, ACTIVE
    // tab chip in the strip (so the user sees they're on a new tab and can switch
    // back) — not a tab-less landing. Reuse-or-create: if an empty "New chat" tab
    // already exists (no messages), just activate it rather than piling up
    // duplicates. We still prune OTHER disposable tabs (keep the strip clean) but
    // never the one the user is about to sit on.
    //
    // A tab is only DISPOSABLE if it carries no conversation AND no insight/PRD
    // work: a PRD/insight tab opens with an empty thread — its insight lives in
    // the opening insight card, not a thread turn — so it must survive "+" even
    // though thread.length === 0. briefMeta covers insight-bound tabs; prd/prdId/
    // evidence + the generating flags cover backlog PRD tabs (which carry no
    // briefMeta) both while generating and once the artifact has landed.
    const disposable = (t: ChatTab) =>
      t.thread.length === 0 &&
      !t.briefMeta && !t.prd && !t.prdId && !t.evidence &&
      !t.prdGenerating && !t.evidenceGenerating
    // Compute the next tabs from the ref (not inside the setTabs updater):
    // updater callbacks run later, during React's render, so an id assigned
    // inside one is still null when setActiveTabId below reads it — which left
    // the fresh "+" tab created but never activated.
    const prev = tabsRef.current
    const existingEmpty = prev.find((t) => disposable(t) && t.title === NEW_CHAT_TITLE)
    let targetId: string
    if (existingEmpty) {
      targetId = existingEmpty.id
      // Drop any OTHER disposable tabs, keep the one we're reusing.
      setTabs(prev.filter((t) => !disposable(t) || t.id === existingEmpty.id))
    } else {
      const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      targetId = id
      // Prune other disposable tabs, then append the fresh "New chat" tab.
      setTabs([...prev.filter((t) => !disposable(t)), {
        id, title: NEW_CHAT_TITLE, thread: [], dbConvId: null, briefMeta: null,
        insightBody: null, prdId: null,
        prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
      }])
    }
    setActiveTabId(targetId)
    setDraft("")
    setActiveConv(null)
    // A new tab is opened to say something, so put the cursor where that starts.
    // Covers the sidebar's "New chat" too — it routes through `/?new=1`, which
    // lands here.
    focusComposerNextFrame()
    // No shared conv-id to reset — each tab tracks its own dbConvId.
  }, [focusComposerNextFrame])

  // ── "New chat" hand-off (`/?new=1`) ───────────────────────────────────────
  // The sidebar's "New chat" affordance pushes `/?new=1` (goToNewChat). The home
  // surface otherwise DEFAULTS to the pinned brief tab on a fresh load, so this
  // one-shot param is what makes "New chat" reliably land on a fresh chat landing
  // instead of the brief. We start a new thread, then strip the param via
  // router.replace so a later refresh doesn't re-open a new chat. Works whether
  // the surface is freshly mounted (param present on first render) or already on
  // screen (the param change re-runs this effect).
  //
  // `consumedNewRef` guards against re-consuming while the param is still present:
  // `useSearchParams()` can hand back a fresh object each render (and startNewThread
  // itself re-renders), so without the latch the effect would loop. It re-arms when
  // the param is absent, so a *subsequent* `/?new=1` nav fires a fresh new-chat.
  const consumedNewRef = useRef(false)
  useEffect(() => {
    const hasNew = searchParams.get("new") != null
    if (!hasNew) {
      consumedNewRef.current = false
      return
    }
    if (consumedNewRef.current) return
    consumedNewRef.current = true
    startNewThread()
    router.replace("/")
  }, [searchParams, startNewThread, router])

  // NOTE: the `/brief?prd=<id>` deep-link (Slack "your PRD is ready" ping) used
  // to be consumed HERE. It's now handled shell-level by
  // `(app)/hooks/useArtifactUrlSync.ts` (mounted once in AppShell), alongside
  // its `?evidence=`/`?ticket=` siblings, so the same param works from any
  // `(app)` page rather than only when ChatScreen happens to be mounted. See
  // that hook for the current implementation + tests.

  // ── "Workbench" hand-off (`/?tab=last`) ───────────────────────────────────
  // The sidebar's two doors into this surface are deliberately split: "Top
  // Insights" (→ /brief) always activates the pinned brief tab, and "Workbench"
  // (→ /?tab=last, goToWorkbench) always activates the user's last CHAT tab, so
  // neither nav can strand you on the other's surface. Resolution order:
  //   1. the remembered tab, if it's still open (it may have been closed since),
  //   2. otherwise the last tab in the strip — the user has open work either way,
  //   3. otherwise a fresh chat tab, so the nav is never a no-op.
  // Latched + param-stripped exactly like the new-chat handler above.
  const consumedLastTabRef = useRef(false)
  useEffect(() => {
    if (searchParams.get("tab") !== "last") {
      consumedLastTabRef.current = false
      return
    }
    if (consumedLastTabRef.current) return
    consumedLastTabRef.current = true
    let remembered: string | null = null
    try { remembered = sessionStorage.getItem(lastTabKey) } catch { /* ignore */ }
    const open = tabsRef.current
    const target =
      remembered && open.some((t) => t.id === remembered)
        ? remembered
        : open.length > 0
          ? open[open.length - 1].id
          : null
    if (target) {
      setActiveTabId(target)
      setDraft("")
    } else {
      startNewThread()
    }
    router.replace("/")
  }, [searchParams, lastTabKey, startNewThread, router])

  const hasThread = thread.length > 0
  // A tab bound to a PRD or brief insight opens with the insight itself as the
  // conversation's first agent message (see the insight turn rendered at the top
  // of the thread) — NOT as a pinned heading above the chat. That message is what
  // anchors the chat to its insight and hosts the Generate/View PRD + prototype
  // actions, so an insight-bound tab always shows the thread view (never the
  // generic "Welcome back" landing) even before the user has sent anything.
  // Also shown while a PRD is still GENERATING (import/resume tabs carry no
  // briefMeta and no prd yet) — the card's button reads "Generating PRD…" and
  // flips to "View PRD" on landing, so the panel is always reopenable from chat.
  // `prdId` counts too: a reloaded chat-task tab has no loaded `prd`, no
  // briefMeta and no in-flight flag, but its persisted prdId proves a PRD
  // belongs to this tab — without it the card (and its View PRD button)
  // vanished after any reload of a task-PRD thread.
  const showInsightMsg = !!(activeTab?.prd || activeTab?.briefMeta || activeTab?.prdGenerating || activeTab?.prdId != null)
  // A resumed tab whose history is still fetching shows the thread view (with
  // a loading skeleton) — never the "Welcome back" landing.
  // Is the just-sent message aimed at the surface currently on screen? Scoped to
  // the tab it was typed in, so switching tabs mid-dispatch doesn't drag the
  // placeholder along. On the landing surface both sides are null, which is the
  // match we want: the first message of a brand-new chat renders instantly there
  // too, before the ask path's openTab has run.
  const pendingSendHere = !!pendingSend && pendingSend.tabId === activeTabId
  const showThreadView = hasThread || showInsightMsg || !!activeTab?.hydrating || pendingSendHere
  // The tab title is "PRD · <insight>"; the message shows the insight sentence on
  // its own (the "PRD" kind is already a chip), so strip the redundant prefix.
  const insightText = (activeTab?.prd?.title ?? activeTab?.title ?? "").replace(/^PRD · /, "")
  // The insight's body/description (from the originating brief finding), shown
  // under the title so the opening card carries the finding's content, not just
  // its heading. Null for tabs not opened from a finding (backlog / plain chat).
  const insightBody = activeTab?.insightBody ?? null
  // Whether a PRD exists for this tab's insight — either loaded on the tab OR
  // saved in the DB (via the brief-prototype map). The tab's `prd` is dropped
  // from localStorage on reload, so relying on it alone made the CTA say
  // "Generate PRD" for an insight that already has one; the DB signal keeps the
  // label ("View PRD") and the action (load, not regenerate) correct after reload.
  const chatPrdExists = !!activeTab?.prd || activeTab?.prdId != null
    || !!(chatInsightState?.hasPrd && chatInsightState.prdId != null)
  // While the brief-prototype map is still loading we don't yet KNOW whether a
  // PRD exists, so committing to "Generate PRD" would flash the wrong label then
  // flip to "View PRD" once the map lands. Show a neutral "Loading…" until we
  // know — but only for an insight-bound tab that has no PRD loaded on it yet
  // (a tab already carrying its prd is authoritative, no wait needed).
  const chatPrdCtaWaiting = !chatPrdExists && !!activeTab?.briefMeta && chatMapLoading
  // What (if anything) the one-button standalone-set row should offer. Null =
  // this chat has no set, so no row renders at all — a plain Q&A reply is not a
  // ticket springboard, exactly as it is not a PRD one. `failed` counts even
  // with no id: a run that died before the row was created still owes the user
  // a way to re-run it.
  const ticketSetActionState: "running" | "ready" | "failed" | null =
    activeTab?.ticketSetRunning ? "running"
    : activeTab?.ticketSetStatus === "failed" ? "failed"
    : activeTab?.ticketSetId != null ? "ready"
    : null
  // Does the active tab's insight already have a saved evidence brief? Check once
  // per insight (cache-read only, no generation) so the chat's first action reads
  // "View Evidence" when evidence exists. Uploaded PRDs / plain chats carry no
  // insight (no briefMeta), so they fall through to the PRD action.
  const activeEvidenceKey = activeTab?.briefMeta
    ? `${activeTab.briefMeta.briefId}:${activeTab.briefMeta.insightIndex}`
    : null
  useEffect(() => {
    if (!activeEvidenceKey || checkedEvidenceRef.current.has(activeEvidenceKey)) return
    checkedEvidenceRef.current.add(activeEvidenceKey)
    const [bId, iIdx] = activeEvidenceKey.split(":").map(Number)
    let cancelled = false
    void (async () => {
      try {
        const ev = await loadEvidenceByInsight(bId, iIdx)
        if (!cancelled && ev) {
          setInsightsWithEvidence((prev) => new Set(prev).add(activeEvidenceKey))
          // The probe just learned which evidence row this tab's insight
          // maps to — remember it so an ask from this tab can ground on
          // it. Only filled where missing: a generation that already
          // stamped a fresher id must not be overwritten by a probe that
          // raced it.
          setTabs((prev) => prev.map((t) =>
            t.briefMeta
              && `${t.briefMeta.briefId}:${t.briefMeta.insightIndex}` === activeEvidenceKey
              && t.evidenceId == null
              ? { ...t, evidenceId: ev.evidenceId }
              : t))
        }
      } catch { /* non-fatal: default to the PRD action */ }
    })()
    return () => { cancelled = true }
  }, [activeEvidenceKey])
  // Evidence exists if it's cached on the tab OR the insight has a saved brief.
  const chatEvidenceExists =
    !!activeTab?.evidence || (activeEvidenceKey != null && insightsWithEvidence.has(activeEvidenceKey))
  // The PRD the chat's prototype button generates/views from (null → disabled).
  const chatProtoPrdId = activeTab?.prdId ?? chatInsightState?.prdId ?? null
  // Whether a ready prototype already exists (from the batch map) — drives the
  // prototype button's View vs Generate face.
  const chatPrototypeReady = !!chatInsightState?.prototypeReady
  // Navigate to an already-built prototype (the CTA's skipExistenceCheck path
  // only GENERATES; the batch map tells us when to VIEW instead).
  const handleViewPrototype = useCallback(() => {
    const pid = chatInsightState?.prototypePrdId ?? chatInsightState?.prdId ?? null
    if (pid != null) router.push(prototypePath(pid))
  }, [chatInsightState, router])
  const displayChips = useMemo(() => {
    const chips = buildHomeChips(homeCards, starters)
    return chips.length > 0 ? chips : DEFAULT_HOME_CHIPS
  }, [homeCards, starters])
  const showChipRow = !hasThread
  const showEmptyStarters = false

  // ── The clarify gate's live batch, as the dock popup's source ───────────────
  // The turn `pendingClarify` names, while its questions are still open. The
  // popup renders from this; the thread shows a one-line pointer in its place.
  // Null once resolved, once the tab's gate clears, or when the thread was
  // rehydrated without the answering machinery — every case where the popup
  // would be a dead surface.
  const pendingClarifyTurn = useMemo(() => {
    const pending = activeTab?.pendingClarify
    if (!pending) return null
    const t = activeTab?.thread.find((tn) => tn.id === pending.turnId)
    return t && t.clarify?.length && !t.clarifyResolved ? t : null
  }, [activeTab])
  const clarifyPopupOpen =
    !!pendingClarifyTurn && !clarifyPopupDismissed[pendingClarifyTurn.id]

  // The assign batch, when the clarify gate isn't holding the dock. Dock
  // priority is clarify > assign > PRD input questions: the gate decides
  // whether a generation even starts, the assign batch is the user's active
  // command, and the PRD's input items keep until both are done.
  const pendingAssignState = activeTab?.pendingAssign
  const assignPopupOpen = !clarifyPopupOpen && !!pendingAssignState?.questions.length
  // The share question queues behind both, on the same precedence rule: a
  // clarify gate decides whether a generation even starts, an assign batch is
  // a command already in flight, and a share is waiting on the user either way.
  const pendingShareState = activeTab?.pendingShare
  const sharePopupOpen =
    !clarifyPopupOpen && !assignPopupOpen && !!pendingShareState?.options.length

  // ── Insight/PRD card + clarifying questions, as reusable nodes ──────────────
  // Same markup, two placements: a HEADER open (brief insight / ideation /
  // backlog load) renders them at the TOP of the thread — the card IS the tab's
  // opening agent message. An IN-CHAT COMMAND open (`prdInFlow`: import a doc,
  // "generate a PRD for X") renders them INLINE, right after the command turn
  // (thread[0]), so the conversation reads in chronological order instead of the
  // PRD card + questions being pinned ABOVE the user's own command message.
  // We anchor to the FIRST turn by INDEX, not a stored id: hydratePrdThread
  // rebuilds the thread from Supabase with fresh turn ids on reload, but thread[0]
  // is still the command turn, so index-anchoring survives rehydrate.
  const insightCardNode = showInsightMsg ? (
    <ChatBubble
      turnId="chat-insight-msg"
      wrapperClassName="bc-turn bc-turn--insight"
      dataTestId="chat-insight-msg"
      agentName={AGENT_NAME}
      agentBadge="Product Coworker"
      agentBodyNode={
        <>
          <div className="bc-insight-msg">
            <span className="bc-insight-msg-kind">PRD</span>
            <span className="bc-insight-msg-text">{insightText}</span>
          </div>
          {/* Insight body — the finding's content under the heading.
              Rendered as markdown so LLM-supplied **bold** shows. */}
          {insightBody ? (
            <div className="bc-insight-msg-body fc-body--md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{insightBody}</ReactMarkdown>
            </div>
          ) : null}
        </>
      }
      footer={
        <ChatArtifactActions
          evidenceExists={chatEvidenceExists}
          prdExists={chatPrdExists}
          prdWaiting={chatPrdCtaWaiting}
          prdGenerating={!!activeTab?.prdGenerating}
          prdLoading={!!activeTab?.prdLoading}
          onViewEvidence={handleOpenEvidence}
          onOpenPrd={handleOpenPrd}
          prototypePrdId={chatProtoPrdId}
          prototypeReady={chatPrototypeReady}
          onViewPrototype={handleViewPrototype}
          onPrototypeSettled={handlePrototypeSettled}
        />
      }
    />
  ) : null
  // "User input needed" items from the PRD, surfaced as chat messages with answer
  // buttons. Answering patches only the affected PRD sections and refreshes the
  // panel live.
  const prdQuestionsNode = activeTab?.prd ? (
    <PrdInputQuestions
      prdId={activeTab.prd.prd_id}
      onPrdUpdated={handleInputPrdUpdated}
      // Popup mode: pending items step through the dock's QuestionPopup, the
      // thread keeps the ✓ record. The clarify gate and an active assign batch
      // outrank them for the dock, so while either is up this hands over
      // `null` and the items hold.
      popupHost={clarifyPopupOpen || assignPopupOpen ? null : questionDockEl}
    />
  ) : null
  // Command-opened PRD tab with at least one turn → render the card + questions
  // INLINE after the command turn; otherwise (header open, or an empty thread)
  // keep them at the TOP as before.
  const inlinePrdCards = !!activeTab?.prdInFlow && thread.length > 0
  // Which turn the inline card anchors AFTER. Precedence: the tab's recorded
  // command-turn id (same-tab generation appends the command mid-thread, and the
  // thread — ids included — persists with the tab); else the last
  // command-looking turn (a fresh-session reopen rehydrates the merged
  // conversation with fresh ids, so the id lookup misses); else thread[0] (the
  // legacy command-opened tab, whose first turn IS the command).
  const inlinePrdAnchorIdx = useMemo(() => {
    if (!inlinePrdCards) return -1
    if (activeTab?.prdFlowTurnId) {
      const i = thread.findIndex((t) => t.id === activeTab.prdFlowTurnId)
      if (i >= 0) return i
    }
    for (let i = thread.length - 1; i >= 0; i--) {
      if (thread[i].query && isPrdCommand(thread[i].query)) return i
    }
    return 0
  }, [inlinePrdCards, activeTab?.prdFlowTurnId, thread])

  // ── Reopen the artifact panel from the tab strip ────────────────────────────
  // Closing the panel (its × or the overlay) used to be one-way: the only route
  // back was the View PRD button on the insight card, which sits at the TOP of
  // the thread — buried by any long conversation, and parked mid-thread on a
  // command-opened tab. This puts the SAME action (handleOpenPrd — sync the
  // cached doc or DB-load this tab's own id; never a regeneration) at the top
  // right of the tab strip, which is chrome: it holds still while the thread
  // scrolls. The in-thread button stays where it is; this is an additional way
  // in, not a replacement.
  //
  // ONE button for every artifact this thread has — PRD, report, or evidence.
  // It opens whichever was written LAST, so it always means "show me what I was
  // just working on" rather than being a PRD button that a report thread has to
  // duplicate. Null (hidden) when the panel is already open, on the brief tab
  // (BriefChat owns its own panel wiring, and handleOpenPrd no-ops there since
  // BRIEF_TAB_ID isn't in `tabs`), or when the tab has no artifact to reopen.
  //
  // The button is icon-only and the icon is FIXED (the strip is chrome, and a
  // labelled pill competed with the tabs for attention). Which document it opens
  // is carried by the tooltip and the accessible name, not by a changing glyph —
  // a glyph that swaps per artifact reads as a different button appearing.
  const reopenArtifact = useMemo(() => {
    if (isBriefTab || contentPanelTab || !activeTabId) return null
    const prdInScope = chatPrdExists || activeTab?.prdGenerating || activeTab?.prdLoading
    const newestReport = threadReports[0] ?? null

    // With a PRD *and* reports in the same thread, the button opens whichever was
    // written LAST — that's the document the user was just working on, and one
    // button that opens "the current artifact" beats two competing ones. Reports
    // are newest-first, so [0] is the thread's newest. A PRD still in flight has
    // no timestamp yet but is by definition the newest thing here, so it wins;
    // otherwise a missing timestamp (a streaming draft) never beats a real one.
    const reportIsNewer = (() => {
      if (!newestReport) return false
      if (!prdInScope) return true
      if (activeTab?.prdGenerating || activeTab?.prdLoading) return false
      const prdAt = activeTab?.prd?.generatedAt
      if (!prdAt) return true
      return new Date(newestReport.created_at).getTime() > new Date(prdAt).getTime()
    })()

    if (reportIsNewer && newestReport) {
      return {
        label: threadReports.length > 1 ? "View reports" : "View report",
        onClick: () => {
          // Reopening lands on the newest report rather than a list — same as the
          // auto-open. The list is behind "All reports" when there's more than one.
          setContent({ reportFocusId: newestReport.id, reportFocusStandalone: false })
          openContentPanel("reports")
        },
      }
    }
    if (prdInScope) return { label: "View PRD", onClick: handleOpenPrd }
    // A thread whose artifact is a standalone ticket set gets the same way back
    // as a PRD or a report. Without this, closing the panel on a PRD-less chat
    // left the tickets reachable only by scrolling the transcript back to the
    // turn that produced them — the strip button is the one affordance that
    // does not move as the thread grows.
    //
    // A FAILED set is excluded on purpose: the reply footer's "Retry tickets"
    // owns that state, and the strip is for reopening something that exists.
    // Note a thread holding both a report and a set shows the report — the
    // newest-wins comparison above needs a timestamp, and the tab carries only
    // the set's id, not when it was written.
    if (activeTab?.ticketSetId != null && activeTab.ticketSetStatus !== "failed") {
      const tabId = activeTab.id
      return { label: "View Tickets", onClick: () => handleTicketSetAction(tabId) }
    }
    if (chatEvidenceExists) return { label: "View Evidence", onClick: handleOpenEvidence }
    return null
  }, [
    isBriefTab, contentPanelTab, activeTabId, chatPrdExists, chatEvidenceExists,
    activeTab?.prdGenerating, activeTab?.prdLoading, activeTab?.prd?.generatedAt,
    activeTab?.id, activeTab?.ticketSetId, activeTab?.ticketSetStatus, handleTicketSetAction,
    handleOpenPrd, handleOpenEvidence, threadReports, setContent, openContentPanel,
  ])

  // ── Tab strip overflow ──────────────────────────────────────────────────────
  // The strip has no scrollbar (a 6px rail inside a 44px strip sat right on the
  // seam where the active tab merges into the content below, and read as grime
  // rather than as chrome). What replaces it: a fade at whichever edge still
  // has tabs past it, plain wheel scrolling, and keeping the active tab in
  // view. Together those make the overflow legible without drawing a rail.
  const tabListRef = useRef<HTMLDivElement | null>(null)
  const tabScrollerRef = useRef<HTMLDivElement | null>(null)

  // Which edges are overflowing → `data-ov="start end"` on the wrapper, which
  // is what fades the pseudo-elements in. Recomputed on scroll, on resize (the
  // artifact panel opening narrows the strip), and whenever the tab set changes.
  const syncTabOverflow = useCallback(() => {
    const list = tabListRef.current
    const scroller = tabScrollerRef.current
    if (!list || !scroller) return
    // 1px of slack: fractional scroll positions otherwise leave a fade stuck on
    // at a hard end.
    const atStart = list.scrollLeft <= 1
    const atEnd = list.scrollLeft + list.clientWidth >= list.scrollWidth - 1
    const edges = [atStart ? null : "start", atEnd ? null : "end"].filter(Boolean).join(" ")
    scroller.setAttribute("data-ov", edges)
    // The left fade must start where the PINNED tab ends, not at the strip's
    // edge — otherwise it paints over the pin (or hides behind it) instead of
    // marking the scrolled-away tabs sliding under it. Measured rather than
    // hard-coded because the pin's width is its label's, which is font-dependent.
    // Measured off bounding rects, not offsetLeft: engines disagree on whether
    // offsetLeft reflects a sticky element's shifted position, and the fade must
    // sit at the pin's VISUAL right edge in both states (at rest and while stuck).
    const pin = list.querySelector<HTMLElement>("[data-tab-pinned='true']")
    const pinW = pin
      ? Math.max(0, pin.getBoundingClientRect().right - scroller.getBoundingClientRect().left)
      : 0
    scroller.style.setProperty("--tab-pin-w", `${pinW}px`)
  }, [])

  // Keep the ACTIVE tab inside the strip's visible corridor — which is narrower
  // than the strip itself at both ends: the sticky Top Insights pin covers the
  // left, and on the right the artifact panel takes up to 60vw off `.main-column`
  // (padding-right), shrinking the strip under whatever is currently scrolled
  // there. So opening the panel on a tab that sat near the right edge left that
  // tab clipped behind the panel with nothing to bring it back. This slides the
  // tabs to its left out of the way until it's fully in the corridor again, and
  // the reverse when the panel closes and the corridor grows back.
  //
  // Minimum movement, not centring: an already-visible tab must not jump.
  const keepActiveTabVisible = useCallback((smooth: boolean) => {
    const list = tabListRef.current
    if (!list) return
    const active = list.querySelector<HTMLElement>("[data-tab-active='true']")
    // The pin is always visible by definition — nothing to scroll to.
    if (!active || active.dataset.tabPinned === "true") return
    const pin = list.querySelector<HTMLElement>("[data-tab-pinned='true']")
    const view = list.getBoundingClientRect()
    const rect = active.getBoundingClientRect()
    const leftBound = pin ? pin.getBoundingClientRect().right : view.left
    // A little air so the tab doesn't sit flush against the panel's edge.
    const rightBound = view.right - 12
    let delta = 0
    if (rect.right > rightBound) delta = rect.right - rightBound
    else if (rect.left < leftBound) delta = rect.left - leftBound
    // A tab wider than the corridor can't satisfy both bounds — prefer its LEFT
    // edge, so the title reads from the start rather than the middle.
    delta = Math.min(delta, rect.left - leftBound)
    if (delta === 0) return
    const left = list.scrollLeft + delta
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches
    // scrollTo is absent in jsdom, hence the guard.
    if (smooth && !reduced && typeof list.scrollTo === "function") {
      list.scrollTo({ left, behavior: "smooth" })
    } else {
      list.scrollLeft = left
    }
  }, [])

  useEffect(() => {
    const list = tabListRef.current
    if (!list) return
    syncTabOverflow()

    // A vertical wheel/trackpad gesture over the strip scrolls it SIDEWAYS —
    // there is nothing to scroll vertically in a 44px strip, so without this
    // the event bubbles and scrolls the thread underneath instead, leaving an
    // overflowed tab unreachable by plain mouse wheel.
    //
    // Wired natively rather than via React's onWheel: React registers wheel
    // listeners at the root as PASSIVE, so preventDefault() there is ignored
    // (and warns) and the thread would still scroll.
    const onWheel = (e: WheelEvent) => {
      if (list.scrollWidth <= list.clientWidth) return
      // A genuine horizontal gesture (trackpad swipe, shift+wheel) already
      // works natively — only redirect the vertical-dominant ones.
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
      e.preventDefault()
      // deltaY is only in PIXELS for trackpads and Chromium mice. A real wheel
      // in Firefox reports LINES (deltaMode 1, deltaY ±3) and some report PAGES
      // (2) — taken raw, a wheel notch there moved the strip 3px and read as
      // "scrolling doesn't work". Convert to pixels before applying.
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? list.clientWidth : 1
      // Instant, not animated — the strip must track the gesture 1:1. That's why
      // `scroll-behavior: smooth` is off on .chat-tab-list (the scrollLeft setter
      // would otherwise obey it and queue an animation per wheel tick).
      list.scrollLeft += e.deltaY * unit
    }

    // ── Drag-to-pan ──────────────────────────────────────────────────────────
    // Press anywhere on the strip and drag sideways to pull the tabs along, the
    // way you'd shove a row of paper tabs. The strip has no scrollbar to grab and
    // a plain mouse has no horizontal wheel, so without this the ONLY way to
    // reach an overflowed tab is a wheel gesture that happens to be over the
    // strip — which is what "I can't scroll it with the mouse" was.
    //
    // Mouse only: touch and pen already pan natively (and hijacking those
    // pointers would fight the browser's own inertia).
    let panning = false
    let panStartX = 0
    let panStartScroll = 0
    let panMoved = false

    const onPointerDown = (e: PointerEvent) => {
      if (e.pointerType !== "mouse" || e.button !== 0) return
      if (list.scrollWidth <= list.clientWidth) return
      // Never start a pan on a control — the × close and the "+" new-tab button
      // must stay ordinary clicks.
      if ((e.target as HTMLElement | null)?.closest("button")) return
      panning = true
      panMoved = false
      panStartX = e.clientX
      panStartScroll = list.scrollLeft
    }

    // A few px of slack before it counts as a drag, so a slightly shaky click on
    // a tab still selects it instead of nudging the strip.
    const PAN_THRESHOLD = 5
    const onPointerMove = (e: PointerEvent) => {
      if (!panning) return
      const dx = e.clientX - panStartX
      if (!panMoved) {
        if (Math.abs(dx) < PAN_THRESHOLD) return
        panMoved = true
        // Marks the drag for CSS (grabbing cursor, no text selection).
        list.classList.add("is-panning")
      }
      // Drag direction is the CONTENT's: pull right → tabs move right → the
      // viewport moves left. Absolute (from the press position), not
      // incremental, so the tabs stay glued to the pointer even if a move event
      // is dropped.
      list.scrollLeft = panStartScroll - dx
      e.preventDefault()
    }

    const endPan = () => {
      if (!panning) return
      panning = false
      list.classList.remove("is-panning")
      // `panMoved` stays set until the click handler below consumes it: the
      // click fires AFTER pointerup, and releasing on top of a tab must not
      // also switch to that tab.
    }

    const onClickCapture = (e: MouseEvent) => {
      if (!panMoved) return
      panMoved = false
      e.preventDefault()
      e.stopPropagation()
    }

    list.addEventListener("scroll", syncTabOverflow, { passive: true })
    list.addEventListener("wheel", onWheel, { passive: false })
    list.addEventListener("pointerdown", onPointerDown)
    // Move/up on the WINDOW so a drag that leaves the 44px strip (easy — it's
    // short) keeps panning, and a release anywhere still ends it.
    window.addEventListener("pointermove", onPointerMove, { passive: false })
    window.addEventListener("pointerup", endPan)
    window.addEventListener("pointercancel", endPan)
    list.addEventListener("click", onClickCapture, true)
    const onResize = () => {
      syncTabOverflow()
      // Instant, not a glide: while the panel animates open (or the user drags
      // its resize handle) this fires continuously, and a queued smooth scroll
      // per frame would stutter. Instant tracking reads as the tabs sliding
      // aside to make room, which is the intent.
      keepActiveTabVisible(false)
    }
    window.addEventListener("resize", onResize)
    // The strip also resizes without a window resize — the artifact panel
    // opening/closing re-lays the column out underneath it.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(onResize) : null
    ro?.observe(list)
    return () => {
      list.removeEventListener("scroll", syncTabOverflow)
      list.removeEventListener("wheel", onWheel)
      list.removeEventListener("pointerdown", onPointerDown)
      window.removeEventListener("pointermove", onPointerMove)
      window.removeEventListener("pointerup", endPan)
      window.removeEventListener("pointercancel", endPan)
      list.removeEventListener("click", onClickCapture, true)
      window.removeEventListener("resize", onResize)
      ro?.disconnect()
    }
  }, [syncTabOverflow, keepActiveTabVisible])

  // Tab opened/closed/renamed → the overflow edges moved.
  useEffect(() => { syncTabOverflow() }, [tabs, syncTabOverflow])

  // Selecting a tab that's scrolled out of view (a command opening a new one,
  // or the palette jumping to an old one) should bring it back, not leave the
  // user hunting for a highlight they can't see. A glide, not a jump — the user
  // didn't drive this scroll directly, so an instant snap reads as the strip
  // flinching.
  useEffect(() => {
    const list = tabListRef.current
    if (!list) return
    // rAF: on a just-opened tab the node lands in the same commit, so measuring
    // before paint would scroll to a stale position.
    const raf = requestAnimationFrame(() => {
      keepActiveTabVisible(true)
      syncTabOverflow()
    })
    return () => cancelAnimationFrame(raf)
  }, [activeTabId, syncTabOverflow, keepActiveTabVisible])

  // The artifact panel opening/closing is the other thing that moves the strip's
  // right edge. The ResizeObserver above tracks the column's padding animating,
  // but only fires while the box actually changes — this is the settle, so the
  // active tab ends up fully clear of the panel (and, on close, so the strip
  // relaxes back now that the corridor is wide again). The delay clears the
  // panel's own 260ms transition; a glide because the strip has stopped moving
  // by then.
  useEffect(() => {
    const t = setTimeout(() => keepActiveTabVisible(true), 300)
    return () => clearTimeout(t)
  }, [contentPanelTab, keepActiveTabVisible])

  return (
    <AppLayout
      mainClassName="main--home-chat"
      mainStyle={{
        maxWidth: "none",
        padding: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        flex: "1 1 auto",
      }}
    >
      <div className="home-chat-root">
        <div className={`od-layout ${railExpanded ? "rail-expanded" : ""}`}>

          {/* Tab bar — always visible. Browser-style: grey strip, the ACTIVE tab
              is a white card (side+top borders, rounded top corners) that merges
              with the white content area below by overlapping the strip's bottom
              border; inactive tabs are plain grey labels on the strip.
              The strip is two parts: the tab list, which SCROLLS once enough
              tabs are open, and the artifact button pinned to its right end,
              which must not — so the strip's chrome (border, background,
              height) sits on this wrapper and only the list scrolls. */}
          <div className="chat-tab-strip">
            {/* The list scrolls; the wrapper carries the edge fades that say so
                (the strip has no scrollbar — see .chat-tab-list in globals.css).
                The fades must sit on a NON-scrolling box or they'd scroll away
                with the tabs, hence the wrapper. */}
            <div className="chat-tab-scroller" ref={tabScrollerRef}>
            <div
              className="chat-tab-list"
              data-testid="chat-tab-bar"
              ref={tabListRef}
              style={{
                display: "flex", alignItems: "stretch", gap: 0,
                flex: "1 1 auto", minWidth: 0,
                // NO padding-left. The strip's 8px lead-in lives inside the
                // pinned tab's own left padding instead — as list padding it was
                // an 8px gap the pin couldn't cover, and scrolling tabs showed
                // through it to the left of the pin.
                overflowX: "auto", overflowY: "visible",
              }}
            >
              {/* Pinned brief tab — always first, never closable (synthesized, not
                  in `tabs`/localStorage). Selecting it renders <BriefChat/> below.
                  PINNED IN THE LITERAL SENSE: `position: sticky` holds it at the
                  strip's left edge while the chat tabs scroll horizontally UNDER
                  it, so the way back to Top Insights is never itself scrolled out
                  of reach. It outranks the scroller's left overflow fade (z-index
                  2), which is offset past it via --tab-pin-w so the fade still
                  reads as "more tabs that way" instead of washing over the pin.
                  `left: 0` — FLUSH, deliberately. Stuck at any inset, the gap
                  between the strip's edge and the pin is a window the scrolling
                  tabs show through; the strip's 8px lead-in is therefore carried
                  as this tab's own extra left padding (22 = 14 + 8), which puts
                  the label in exactly the same place but backs it with the pin's
                  opaque fill all the way to the edge. */}
              <div
                key={BRIEF_TAB_ID}
                className="chat-tab chat-tab--pinned"
                data-tab-active={isBriefTab ? "true" : undefined}
                data-tab-pinned="true"
                onClick={() => { setActiveTabId(BRIEF_TAB_ID); setDraft("") }}
                style={{
                  position: "sticky", left: 0, zIndex: 3,
                  display: "flex", alignItems: "center", gap: 6,
                  padding: "0 14px 0 22px", fontSize: 13, cursor: "pointer",
                  color: isBriefTab ? "var(--ink, #1A1A17)" : "var(--ink-3, #8C8A84)",
                  fontWeight: isBriefTab ? 500 : 400,
                  // OPAQUE even when inactive (the strip's own colour, so it looks
                  // unchanged at rest) — a transparent pin would let the scrolling
                  // tabs show straight through it.
                  background: isBriefTab ? "var(--surface, #fff)" : "var(--surface-2, #f7f5f0)",
                  borderTop: isBriefTab ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                  borderLeft: isBriefTab ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                  borderRight: isBriefTab ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                  borderRadius: "8px 8px 0 0",
                  marginTop: 8, marginBottom: -1,
                  whiteSpace: "nowrap", transition: "color 0.12s, background 0.12s, border-color 0.12s",
                  userSelect: "none", flexShrink: 0,
                }}
              >
                <span style={{ lineHeight: "1.3" }}>Top Insights</span>
              </div>
              {tabs.map((tab) => {
                const isActive = activeTabId === tab.id
                return (
                  <div
                    key={tab.id}
                    className="chat-tab"
                    data-tab-active={isActive ? "true" : undefined}
                    onClick={() => { setActiveTabId(tab.id); setDraft(""); focusComposerNextFrame() }}
                    style={{
                      // Positioned so the separator / shoulder pseudo-elements
                      // anchor to it; raised when active so its shoulders, which
                      // reach 8px into both neighbours, paint OVER them (a later
                      // sibling would otherwise cover the right one).
                      position: "relative",
                      zIndex: isActive ? 1 : undefined,
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "0 10px 0 14px", fontSize: 13, cursor: "pointer",
                      color: isActive ? "var(--ink, #1A1A17)" : "var(--ink-3, #8C8A84)",
                      fontWeight: isActive ? 500 : 400,
                      background: isActive ? "var(--surface, #fff)" : "transparent",
                      borderTop: isActive ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                      borderLeft: isActive ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                      borderRight: isActive ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                      borderRadius: "8px 8px 0 0",
                      marginTop: 8, marginBottom: -1,
                      whiteSpace: "nowrap", transition: "color 0.12s, background 0.12s, border-color 0.12s",
                      userSelect: "none", flexShrink: 0,
                    }}
                  >
                    <span style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", lineHeight: "1.3" }}>
                      {tab.title}
                    </span>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); closeTab(tab.id) }}
                      style={{
                        display: "flex", alignItems: "center", justifyContent: "center",
                        width: 16, height: 16, flexShrink: 0,
                        background: "none", border: "none", cursor: "pointer",
                        fontSize: 13, color: "var(--ink-4, #B0AEA6)", padding: 0, lineHeight: 1,
                        borderRadius: 3,
                      }}
                      title="Close tab"
                    >×</button>
                  </div>
                )
              })}
              {/* New-tab button — styled like Chrome's: a small rounded control
                  just to the right of the last tab, vertically centered in the
                  strip, with a subtle circular highlight on hover. */}
              <button
                type="button"
                onClick={startNewThread}
                aria-label="New chat"
                title="New chat"
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--line, #E8E6E0)" }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent" }}
                style={{
                  display: "flex", alignItems: "center", justifyContent: "center",
                  width: 28, height: 28, margin: "8px 4px 0 6px", padding: 0,
                  background: "transparent", border: "none", cursor: "pointer",
                  borderRadius: "50%", fontSize: 18, lineHeight: 1,
                  color: "var(--ink-3, #8C8A84)", flexShrink: 0,
                  transition: "background 0.12s",
                }}
              >+</button>
            </div>
            </div>
            {/* Project signal (main-chat entry flow). When THIS chat's PRD
                silently forked a project (`content.activeProjectId`), the header
                morphs to say so and gives a way to jump straight into that
                project's chat — otherwise the user gets no sign a project now
                exists (the fork happens behind the scenes). Pinned to the
                strip's right end beside the artifact-reopen control; hidden
                entirely when no project is bound, so a normal chat is
                byte-identical to before. Lands on the caller's own (individual)
                project chat, the same target the main-chat PRD-fork nav uses. */}
            {content.activeProjectId != null ? (
              <button
                type="button"
                className="chat-project-jump"
                data-testid="chat-open-project"
                title="Open the project created from this chat"
                onClick={() =>
                  router.push(`/projects?id=${content.activeProjectId}&chat=individual`)
                }
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  flexShrink: 0, alignSelf: "center", marginLeft: 8,
                  height: 26, padding: "0 11px",
                  background: "var(--surface-2, #f7f5f0)",
                  border: "1px solid var(--line, #E8E6E0)",
                  borderRadius: 999, cursor: "pointer",
                  fontSize: 12, fontWeight: 500,
                  color: "var(--ink-2, #3d3a34)", whiteSpace: "nowrap",
                }}
              >
                <IconFolder size={14} />
                <span>Open project</span>
              </button>
            ) : null}
            {/* The way back to a closed artifact panel. Pinned to the strip's
                right end — outside the scrolling list — so no number of open
                tabs and no scroll position can put it out of reach. Hidden
                while the panel is open (it has its own close) and on tabs with
                no artifact to reopen. */}
            {reopenArtifact ? (
              <button
                type="button"
                className="chat-artifact-reopen"
                data-testid="chat-reopen-artifact"
                title={`${reopenArtifact.label} — reopen the panel`}
                aria-label={reopenArtifact.label}
                onClick={() => { void reopenArtifact.onClick() }}
              >
                {/* ONE control, one icon, whatever it opens. The strip is chrome:
                    a glyph that changes per artifact type reads as a different
                    button appearing, when it is the same "open what this thread
                    has" affordance throughout. The label says which document. */}
                <IconDocument size={15} />
              </button>
            ) : null}
          </div>

          {isBriefTab ? (
            // Pinned brief tab → the full top-insights surface. ChatScreen already
            // provides AppLayout, so BriefChat renders bare (it owns its own
            // header + finding cards + composer + content-panel wiring).
            <BriefChat />
          ) : (
          (() => {
            const mainTurns = mapMainTurns(thread, {
              animatedTurnIds, askStartRef, resumedTurnsRef, lastLiveTurnIdx,
              busy, activeTab, name, userInitials, skillForQuery,
              ticketSetActionState, showInsightMsg, chatEvidenceExists,
              chatPrdExists, chatPrdCtaWaiting, chatProtoPrdId, chatPrototypeReady,
              inlinePrdCards, inlinePrdAnchorIdx, insightCardNode, prdQuestionsNode,
              clarifyPopupOpen, pendingClarifyTurn,
              handleAskAgain, handleStopAsk, submitClarifyAnswers, setViewerAttachment,
              openReportByTitle, openArtifactInPanel, openChatArtifactItem,
              handleTicketSetAction, handleOpenEvidence, handleOpenPrd,
              handleViewPrototype, handlePrototypeSettled,
              // share_to_slack — the preview card riding a turn. The SEND is
              // the only one of these that reaches Slack, and only after the
              // user presses the button in the card.
              onSendSlackShare: (turnId, channelId, note) =>
                void sendSlackShare(activeTab!.id, turnId, channelId, note),
              onCancelSlackShare: (turnId) =>
                patchSlackShare(activeTab!.id, turnId, {
                  resolved: { outcome: "cancelled" },
                }),
              onPickSlackShareTarget: (turnId, target) =>
                void repreviewSlackShare(activeTab!.id, turnId, target),
            })
            // The main-chat shell region, rendered through the shared <ChatShell>
            // in controlled mode: turns are pre-mapped here, refs and scroll
            // behaviour stay host-side, and the composer, pending-send bubble, and
            // dock extras are host-rendered and passed as slots. A surface:"main"
            // descriptor is a structural no-op — no project seam is reachable.
            const landingNode = (
                <div className="home-landing-eyeline">
                  <div className="od-center-inner od-center-inner--home">
                    <div className="chat-greeting">
                      <h1 className="chat-greeting-title">
                        Welcome back, <em>{name}</em>.
                      </h1>
                      <p className="chat-greeting-sub">
                        Welcome to Sprntly — what would you like to work on?
                      </p>
                    </div>

                    <div className="home-landing-composer">
                      {renderComposer(true)}
                      {showChipRow ? (
                        <div className="home-chip-row home-chip-row--under-chat" role="list">
                          {displayChips.map(({ kind, card }) => (
                            <button
                              key={`${kind}-${card.id}`}
                              type="button"
                              className={`home-chip${kind === "starter" ? " home-chip--muted" : ""}`}
                              role="listitem"
                              onClick={() =>
                                kind === "home"
                                  ? handleHomeCard(card)
                                  : handleStarterChip(card.prompt ?? card.title)
                              }
                            >
                              <span className="home-chip-icon" aria-hidden>
                                <ChatSuggestionIcon id={card.icon} size={16} />
                              </span>
                              <span className="home-chip-label">{card.title}</span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>

                    {showEmptyStarters ? (
                      <EmptyPane
                        title="No starter prompts yet"
                        hint="Populate `homeStarterCards` and `ondemandStarters` from your API or org defaults."
                        placeholders={4}
                      />
                    ) : null}
                  </div>
                </div>
            )
            const leadingNode = (
                            <>
                              {/* Insight message — for a HEADER open, the chat opens with its
                                  insight as the agent's first message (a pinned heading at the
                                  top). For an IN-CHAT COMMAND open (`inlinePrdCards`) the card
                                  + questions instead render inline after the command turn
                                  (`afterNode` above). Hosts the Generate/View PRD +
                                  Generate/View Prototype actions. */}
                              {!inlinePrdCards ? insightCardNode : null}
                              {!inlinePrdCards ? prdQuestionsNode : null}
                              {/* Resumed-conversation loading state: the tab opened
                                  instantly on row click; its history is still in flight. */}
                              {activeTab?.hydrating && thread.length === 0 ? (
                                <ChatBubble
                                  turnId="chat-hydrating"
                                  ariaBusy
                                  agentName={AGENT_NAME}
                                  agentBadge={null}
                                  agentBodyNode={
                                    // Nothing is generating here — history is loading —
                                    // so this keeps its own copy ("loading conversation…",
                                    // which used to sit in the head above) rather than
                                    // inheriting the ask's "Working on your question".
                                    <AssistantThinkingSkeleton compact phase="loading conversation…" />
                                  }
                                />
                              ) : null}
                            </>
            )
            const pendingSendNode = pendingSendHere && pendingSend ? (
                      <ChatBubble
                        turnId="pending-send"
                        dataTestId="pending-send"
                        ariaBusy
                        user={{
                          name,
                          initials: userInitials,
                          query: pendingSend.query,
                          // Name-only and inert here, exactly as the optimistic
                          // turn renders them before extraction — no `content`/
                          // `downloadable` means ChatBubble's own card renders
                          // non-viewable, same as this block always did.
                          attachments: pendingSend.attachments.map((a) => ({ name: a.name })),
                        }}
                        agentName={AGENT_NAME}
                        agentBadge="Product Coworker"
                        agentBodyNode={
                          // The same ladder the real turn will pick up — and
                          // the same clock, handed over with the turn — so a
                          // send opens on rung 0 (nothing) rather than a
                          // spinner that flickers for 300ms on a cache hit.
                          <AssistantWaitState
                            compact
                            startedAt={pendingSend.startedAt}
                            skillLabel={skillForQuery(pendingSend.query)?.label ?? null}
                            longSkill={isLongRunningSkill(skillForQuery(pendingSend.query)?.id)}
                          />
                        }
                      />
                    ) : null
            const dockExtras = (
              <>
                {clarifyPopupOpen && pendingClarifyTurn?.clarify ? (
                  <QuestionPopup
                    questions={pendingClarifyTurn.clarify.map((cq) => ({
                      header: cq.header ?? null,
                      prompt: cq.prompt,
                      options: cq.options.map((o) => ({ label: o })),
                      skipDefault: cq.skip_default,
                    }))}
                    fallbackHeader="PRD details"
                    busy={busy || !!activeTab?.prdGenerating}
                    onDismiss={() =>
                      setClarifyPopupDismissed((p) => ({ ...p, [pendingClarifyTurn.id]: true }))
                    }
                    onComplete={(answers) => {
                      const given = answers
                        .filter((a) => !a.skipped && a.answer)
                        .map((a) => ({ prompt: a.prompt, answer: a.answer }))
                      // Everything skipped is a skip in everything but name —
                      // submitClarifyAnswers([]) resolves it as one, same as the
                      // card's empty submit.
                      void submitClarifyAnswers(given)
                    }}
                  />
                ) : null}
                {/* The assign batch. Picks are LOCAL until the last question
                    settles — then completeAssign writes every pair through
                    PUT /fields and posts the summary. Closing early therefore
                    writes nothing. */}
                {assignPopupOpen && pendingAssignState && activeTabId ? (
                  <QuestionPopup
                    questions={pendingAssignState.questions.map((q) => ({
                      header: q.header,
                      prompt: q.prompt,
                      options: q.options.map((o) => ({
                        label: o.label,
                        description: o.description ?? null,
                        value: o.value,
                      })),
                      // Free text can't be validated against the roster — the
                      // options ARE the answer space here.
                      allowOther: false,
                      // "Assign 2 tickets to X" → the backend marks the
                      // person-fixed question multi, and the card renders as
                      // tick-several-confirm-once instead of a single pick
                      // that could only honour one of the asked-for tickets.
                      multiSelect: !!q.multi,
                    }))}
                    fallbackHeader="Assign"
                    onComplete={(answers) => void completeAssign(activeTabId, answers)}
                    onDismiss={() => cancelAssign(activeTabId)}
                  />
                ) : null}
                {/* The share question — which channel, or which document.
                    Every choice this product asks for comes through here
                    (owner's directive, 2026-08-16); the preview card renders
                    the MESSAGE, never the picker. Answering re-previews
                    server-side, so a private channel Sprntly can't join is
                    still caught after the pick. Dismissing settles the share
                    as not-sent rather than leaving it hanging. */}
                {sharePopupOpen && pendingShareState && activeTabId ? (
                  <QuestionPopup
                    questions={[{
                      header: pendingShareState.header,
                      prompt: pendingShareState.prompt,
                      options: pendingShareState.options,
                      // Channels: free text is a real answer — a workspace can
                      // have more channels than anyone wants to scroll, and the
                      // typed name is matched server-side exactly like a picked
                      // one. Documents: the candidates ARE the answer space.
                      allowOther: pendingShareState.kind === "channel",
                    }]}
                    fallbackHeader="Share"
                    onComplete={(answers) =>
                      void completeShareQuestion(activeTabId, answers)}
                    onDismiss={() => cancelShareQuestion(activeTabId)}
                  />
                ) : null}
                {/* Portal slot for lower-priority question batches (PRD input
                    questions, assignment questions). Empty div when nothing
                    portals in — costs no height. */}
                <div className="bc-question-dock" ref={setQuestionDockEl} />
                {/* Renders NOTHING when there are no suggestions — no empty
                    container, no reserved height — so a thread Sprntly has
                    nothing to add to looks exactly as it did before this
                    feature, and a late response never shifts the composer
                    under the user's cursor. Active tab only: `suggestionsByTab`
                    is keyed by tab so a background answer's chips stay with
                    their own thread. */}
                <NextPromptSuggestions
                  suggestions={(activeTabId && suggestionsByTab[activeTabId]) || []}
                  disabled={busy}
                  onPick={(prompt) => { void submitAsk(prompt) }}
                />
              </>
            )
            return (
              <ChatShell
                descriptor={{
                  surface: "main",
                  frame: {
                    mode: showThreadView ? "thread" : "landing",
                    landing: landingNode,
                    viewportClassName: "od-center-scroll",
                  },
                  refs: {
                    viewportRef: threadScrollRef,
                    onViewportScroll: handleThreadScroll,
                    contentColumnRef: setThreadContentEl,
                  },
                  transcript: {
                    agentName: AGENT_NAME,
                    agentBadge: "Product Coworker",
                    timestamps: "none",
                    leading: leadingNode,
                  },
                  composer: {
                    busyMode: "block-while-asking",
                    stop: { enabled: true, onStop: handleStopAsk },
                    attachments: true,
                  },
                  reply: { mode: "streamed" },
                  send: { onSubmit: handleComposerSubmit, pendingSendBubble: true },
                  dock: { aboveComposer: dockExtras },
                }}
                turns={mainTurns}
                pendingSend={pendingSendNode}
                composerNode={renderComposer(false)}
              />
            )
          })()
          )}
        </div>
      </div>
      {viewerAttachment ? (
        <AttachmentViewer attachment={viewerAttachment} onClose={() => setViewerAttachment(null)} />
      ) : null}
    </AppLayout>
  )
}
