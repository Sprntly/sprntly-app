"use client"

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { useAuth } from "../../../lib/auth"
// `crucibleOn` gates Goal Analysis. `dispatchChatIntent` / `useChatIntentExecutors`
// that main re-adds here are already owned by the shared engine (useConversation),
// so they are NOT reintroduced into this wrapper.
import { chatIntentEnvelopeOn, crucibleOn } from "../../../lib/onboarding/types"
import { ChatArtifactActions } from "../../shared/chat-shell/ChatArtifactActions"
import { useNextPrompts, type NextPromptsAdapter } from "../../shared/chat-shell/useNextPrompts"
import { openArtifactDestination } from "../../shared/chat-shell/openArtifactDestination"
import type { ChatHomeCard, ConversationRow } from "../../../types/content"
import { buildHomeChips, DEFAULT_HOME_CHIPS } from "../../../lib/homeChips"
import { AppLayout } from "./AppLayout"
import { BriefChat, isPrdCommand, isPrdEditCommand, isTicketsCommand, mentionsPrd, prdCommandTask } from "../../shared/BriefChat"
import { PrdInputQuestions, clearPrdDrafts, prdStateFromRecord } from "../../shared/PrdInputQuestions"
import {
  clarifyAnswersText,
  clarifyQuestionsText,
  type ClarifyAnswer,
  type ClarifyQuestion,
  type ClarifyResolution,
} from "../../shared/ClarifyQuestionsCard"
import type {
  GoalGate, GoalGateResolved, SettledPlan,
} from "../../shared/GoalGateCard"
import type { PlanDecision } from "../../shared/GoalAnalysisPlan"
import { type PopupAnswer } from "../../shared/QuestionPopup"
import {
  useSlackShareCardHandlers,
  type PendingShareState,
} from "../../shared/chat-shell/conversation/useSlackShareCardHandlers"
import { useAssignCompletion } from "../../shared/chat-shell/conversation/useAssignCompletion"
import { askAgain } from "../../shared/chat-shell/conversation/askAgain"
import { runClarifiedGeneration as runSharedClarifiedGeneration } from "../../shared/chat-shell/conversation/clarifiedGeneration"
import {
  SlackShareMessage,
  type SlackShareResolution,
} from "../../shared/SlackSharePreviewCard"
import { IconDocument, IconSparkle } from "../../shared/app-icons"
import { AttachmentViewer } from "../../shared/AttachmentViewer"
import { IconFolder } from "@tabler/icons-react"
// The strip's reopen button is icon-only, so the Evidence case needs an icon of
// its own — the same one ContentPanel's Evidence tab wears, so the button reads
// as "reopen that tab".
import { DRAFT_MAX_CHARS, DRAFT_MIN_CHARS, type PinnedSkill } from "../../shared/ChatComposer"
// Highlight-to-reply / edit-a-past-prompt: one definition of how a quoted
// passage rides a message, shared with the mapper and the persistence rewind.
import { buildQuotedMessage, normalizeQuote, splitQuotedSuffix } from "../../../lib/chatQuote"
import {
  type ChatIntentEnvelope,
  ApiError, apiErrorMessage, artifactsApi, askApi, attachmentsApi, chatSuggestionsApi, goalAnalysisApi, storiesApi, type AskResponse, type ChatArtifactItem, type GoalRunDetail, type OpenArtifactCandidate, type OpenArtifactResult, type ReportSummary, type SlackSharePreview, type SlackShareTargetRef, type TicketAssignQuestion,
} from "../../../lib/api"
import { createChatPersistence, replyToText } from "../../../lib/chatPersistence"
import { addToSet, isComposerBusy, removeFromSet } from "../../../lib/chatAskState"
import { runPrdGeneration, resumePrdGeneration, runPrdGenerationFromIdeation, loadPrdById } from "../../../lib/runPrdGeneration"
// resumePrdGeneration re-enters polling for an already-kicked-off PRD (the import path).
import type { PrdTabRequest } from "../../../context/NavigationContext"
import { runEvidenceGeneration, resumeEvidenceGeneration, loadEvidenceByInsight } from "../../../lib/runEvidenceGeneration"
import { resumeAskGeneration, getPendingAsk, AskCancelledError, AskStoppedError, AskTimeoutError } from "../../../lib/runAskGeneration"
import { GROUNDED_PROGRESS_ENABLED } from "../../../lib/friendlyPhase"
// The ONE owner of a standalone ticket-set run and of `content.ticketSet`.
// Nothing in this file may call `storiesApi.generateFromInsight` directly —
// see the module header for why a second caller is a second LLM bill.
import { loadTicketSet } from "../../../lib/runTicketSetGeneration"
import { getPendingJob, insightScope } from "../../../lib/jobResume"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import type { DetailState, PrdState, PrdContent } from "../../../types/content"
import { useBriefPrototypeMap } from "../../design-agent/useBriefPrototypeMap"
import { prototypePath } from "../../../lib/routes"
import { documentPath } from "../../../(app)/artifacts/doc/DocumentRoute"
import { ChatBubble } from "../../shared/ChatBubble"
import { ChatTranscript, type ChatTranscriptTurn } from "../../shared/ChatTranscript"
import { ConversationView } from "./ConversationView"
import { useConversation } from "./useConversation"
import { useThreadScroll } from "./useThreadScroll"
import { useComposer } from "./useComposer"
import type { MapMainTurnsDeps } from "../../shared/chat-shell/types"
import { resolveShareRef } from "../../shared/chat-shell/conversation/resolveShareRef"
import { useDocumentReopenProbe } from "../../shared/chat-shell/conversation/useDocumentReopenProbe"
import { matchReportByTitle } from "../../shared/chat-shell/conversation/matchReportByTitle"
import type { PrdRecord } from "../../../lib/api"
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
  /** The `conversation_turns.id` this turn was persisted as, when known.
   *
   *  Only needed to REWIND the conversation to this turn (editing or retrying a
   *  past prompt): the server needs the row id, and the client turn id is its
   *  own invention. Stamped on restore (`buildRestored`, straight off the DB
   *  row) — turns sent in THIS session are resolved from `chatPersistence`'s own
   *  map instead, since their id only arrives after the write settles. Absent on
   *  every other path, which makes the rewind a no-op rather than a guess. */
  dbTurnId?: number
  /** Multi-party attribution (project GROUP surface only). Present ONLY on a
   *  turn authored by SOMEONE OTHER than the current viewer — a peer's message
   *  in the shared group thread. The single-author surfaces (main, private) and
   *  the viewer's OWN group turns leave this UNSET, so `mapMainTurns` renders
   *  them through the identical default (right-aligned, the viewer's own head)
   *  path — the author render arm is data-driven and inert without this field.
   *  `initials`/`avatarStyle` are PRECOMPUTED by the group adapter (via
   *  `avatarColor.personAvatarStyle`) so the shared mapper never imports a
   *  project-side helper. The agent's own turns carry NO author (they render as
   *  Sprntly through the existing agent path). */
  author?: {
    name: string
    role?: string | null
    userId?: string | null
    initials?: string | null
    avatarStyle?: CSSProperties | null
  }
  /** Project GROUP surface: a user message that was POSTED to the shared thread
   *  with the agent intentionally NOT addressed — a multi-member group turn that
   *  does not `@Sprntly`-mention it (the 2-mode response gate's post-only branch),
   *  or its hydrated/replayed form. The agent was never going to reply, so this
   *  turn renders with NO agent block at all — no thinking state, and crucially
   *  no "No response was generated" placeholder (which reads like a failure). It
   *  is a peer-style silent post; the viewer's own head/alignment are unchanged.
   *  This is distinct from a genuinely dropped reply (e.g. a clarify state lost on
   *  reload), which SHOULD still surface as a real no-response. Unset on main,
   *  private, solo groups, and `@Sprntly`-tagged group turns → default agent
   *  rendering, byte-identical to before. */
  postedOnly?: boolean
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
  /** A Goal Analysis gate riding this turn — the definition question, or the
   *  plan awaiting approval. Rendered IN THE THREAD as an answerable card, the
   *  same contract `clarify` uses: live while unresolved, replaced by a settled
   *  summary once `goalGateResolved` lands. Both gates are the conversation
   *  that lets a PM defend the result, so they belong in the conversation. */
  goalGate?: GoalGate
  /** What the user actually agreed to. Its presence flips the card to its
   *  settled form and is what keeps the record in the thread. */
  goalGateResolved?: GoalGateResolved
  /** A refusal the reader can act on, shown BESIDE the live gate. Not `error`:
   *  that renders the thread's generic "There was an interruption, try again."
   *  and discards the message, and it replaces the card — turning a run still
   *  sitting at its gate server-side into a dead end with no retry. */
  goalGateError?: string
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
  /** Curated, user-facing progress copy for the pipeline leg currently running
   *  (e.g. "Looking through your connected sources…"), from the real backend
   *  `phase` SSE signal. Only set when the grounded-progress flag is on; drives
   *  the wait line ahead of the answer. Transient, cleared on every terminal
   *  transition, like `partial`. */
  livePhase?: string
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

type BriefMeta = { briefId: number; insightIndex: number }

export type ChatTab = {
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
 *  generation: the shared error card says "There was an interruption, try
 *  again." — which is not what happened to an open that simply found the
 *  document busy. */
function openFailureReply(detail: string): AskResponse {
  const reason = detail.trim().replace(/[.\s]+$/, "")
  return {
    answer: reason
      ? `I couldn't open that PRD — ${reason.charAt(0).toLowerCase()}${reason.slice(1)}. Try again in a moment.`
      : "I couldn't open that PRD just now. Try again in a moment.",
    key_points: [], citations: [], confidence: 1, unanswered: "",
  } as AskResponse
}

/** Pressing Enter while an ask is in flight used to be a silent no-op — the
 *  keystroke simply vanished, with the draft still sitting there. The guard is
 *  correct (one ask per tab); the silence was the bug. */
export const BUSY_ENTER_HINT_LEAD = "Sprntly is still answering. Your message is saved — send it when the answer lands, or "
export const BUSY_ENTER_HINT_TAIL = " to interrupt."
/** How long a copied-prompt tick stays on the button. Long enough to be seen
 *  after the click that caused it, short enough not to read as state. */
const COPIED_HINT_MS = 1600

// Main's next-prompt fetch: the shared endpoint keyed by the settled
// conversation id. A stable module const so the shared hook's fetch-after-settle
// callback keeps a stable identity across renders.
const MAIN_NEXT_PROMPTS_ADAPTER: NextPromptsAdapter = {
  fetchSuggestions: (conversationId, opts) =>
    chatSuggestionsApi.next(conversationId, opts).then((r) => r.suggestions),
}

/** Drop a `pending` goal gate coming back from storage.
 *
 *  Stripped on SAVE too, but a thread written by an older build still carries
 *  one — and a restored `pending` gate is a spinner nothing can fill: the poll
 *  that would replace it with the run's question died with the page that
 *  started it. Worse, it would satisfy the restore's has-this-run guard against
 *  itself and block the rebuild that replaces it. Answered gates survive: a
 *  settled definition or plan is a record, not an indicator.
 */
function _thawThread(thread: ThreadTurn[] | undefined): ThreadTurn[] {
  return (thread ?? []).map((tn) =>
    (tn.goalGate?.kind === "pending" ? { ...tn, goalGate: undefined } : tn))
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
    pendingDocumentQuote,
    setPendingDocumentQuote,
    pendingChatHandoff,
    setPendingChatHandoff,
    pendingPrdTab,
    setPendingPrdTab,
    openPrdTab,
    pendingReportFocus,
    setPendingReportFocus,
    pendingTicketSetFocus,
    pendingDocumentFocus,
    setPendingDocumentFocus,
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
  // DEFAULT OFF, allowlist-only. This hides the entry point; the backend route
  // refuses the request on its own, because the client decides what to render
  // and the server decides what runs.
  const goalAnalysisOn = crucibleOn(workspace?.feature_flags)
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
        thread: _thawThread(t.thread),
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
  // The document currently in the panel, readable AFTER an await — the
  // document resume probe uses it to refuse to overwrite a generation that
  // started while its list read was in flight (the stale-read rule
  // `useThreadDocumentSync` states for the same fetch).
  const contentDocumentIdRef = useRef<number | null>(null)
  contentDocumentIdRef.current = content.documentId ?? null
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
          id: t.id ?? "", title: t.title ?? "", thread: _thawThread(t.thread),
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

  // Persist tabs to sessionStorage lives BELOW the composer destructure (it folds
  // in the optimistic `pendingSend`); see the effect after `} = composer`.
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
  /** Whether the active tab's thread is still being fetched. A dependency of
   *  the Goal Analysis restore, which must wait for hydration and then RUN —
   *  not bail once and never look again. */
  const activeTabHydrating = !!activeTab?.hydrating

  /** Patch one turn in one tab. Declared up here because the Goal Analysis
   *  restore effect runs above the gate handlers that share it. */
  const setTabsGoalGate = useCallback(
    (tabId: string, turnId: string, patch: Partial<ThreadTurn>) => {
      setTabs((prev) => prev.map((t) => (t.id === tabId
        ? {
            ...t,
            thread: t.thread.map((tn) =>
              (tn.id === turnId ? { ...tn, ...patch } : tn)),
          }
        : t)))
    },
    [],
  )
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
  // Per-conversation composer (draft, attachments, slash palette + pinned skill,
  // `+` menu, busy hint, dictation, optimistic pending-send) — extracted verbatim
  // into the shared unit. The skill/slash-filter wiring, the submit/input/keydown
  // handlers, and the composer effects at other positions stay in the host below
  // and read this hook's state through the destructure.
  // The whole composer is captured as one object so it can be handed to the
  // engine (which drives the send handlers off it); the tab orchestrator still
  // reads its state through this destructure.
  const composer = useComposer({ showToast })
  const {
    draft, setDraft,
    pendingSend, setPendingSend,
    showSlash, setShowSlash,
    slashFilter, setSlashFilter,
    slashActive, setSlashActive,
    slashFromMenu, setSlashFromMenu,
    pinnedSkill, setPinnedSkill,
    plusMenuOpen, setPlusMenuOpen,
    plusMenuActive, setPlusMenuActive,
    composerHint, setComposerHint, showComposerHint,
    attachments, setAttachments,
    composerRef,
    focusComposerNextFrame,
    fileInputRef,
    voiceBaseRef,
    voice, handleToggleVoice,
    handleFileSelect,
    openSkillPalette,
    handleComposerInput,
    handleSlashSelect,
    handlePlusMenuSelect,
    skills, setSkills,
    filteredSkills, slashOpen,
    skillForQuery,
  } = composer

  // Persist tabs to sessionStorage (session-scoped; see the key comment above) —
  // strip large/transient fields (prd, evidence, *Generating). Placed AFTER the
  // `composer` destructure because it also folds in the optimistic `pendingSend`.
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
              // A `pending` goal gate is the same kind of in-flight indicator
              // as the two above, and dies the same way: the poll that would
              // replace it with the run's question lives in the page that just
              // went. Restored, it is a permanent "working out what this goal
              // means…" that nothing can fill — and worse, it satisfies the
              // restore's own has-a-gate guard and blocks the rebuild that
              // would have replaced it. The ANSWERED gates persist: a settled
              // definition or plan is a record, not a spinner.
              turn.goalGate?.kind === "pending"
                ? { ...turn, goalGate: undefined }
                : turn.slackShare?.busy
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
      // Fold the OPTIMISTIC in-flight send into the SAVED copy only. Between
      // `setPendingSend()` and `resolveSendTarget()` (a slow intent-classify /
      // clarify round-trip in between) the just-sent question lives ONLY in the
      // transient `pendingSend` overlay — never in `tabs` — so a reload during
      // that window landed on a blank "New chat" with the question gone. Writing
      // it into the persisted snapshot here (NOT into React `tabs` — live DOM is
      // untouched) makes the question survive the reload. It is marked
      // `interrupted` because no server-side ask exists yet in this pre-dispatch
      // window (the ask_id is minted later, inside runConversationAsk), so there
      // is nothing to re-attach to — the restore shows the question with a
      // one-click "send it again" rather than a stranded spinner. The moment
      // `resolveSendTarget` seeds the REAL awaiting turn (and `pendingSend`
      // clears in the same tick), the tab's last turn is awaiting and the guard
      // below skips the fold — leaving the working resume-on-reload path (turn
      // in `tabs` + persisted ask_id) exactly as it was.
      const withPending = (() => {
        if (!pendingSend || !pendingSend.query || pendingSend.tabId == null) return slim
        return slim.map((t) => {
          if (t.id !== pendingSend.tabId) return t
          const last = t.thread[t.thread.length - 1]
          // A real awaiting turn is already here (the seeded ask) — the resume
          // path owns it; do not double it.
          if (last && last.reply === undefined && last.error === undefined && !last.stopped) return t
          const pendingTurn = {
            id: `pending-${pendingSend.startedAt}`,
            query: pendingSend.query,
            ...(pendingSend.attachments && pendingSend.attachments.length
              ? { attachments: pendingSend.attachments }
              : {}),
            interrupted: true,
          }
          const title = t.title && t.title !== "New chat" ? t.title : pendingSend.query.slice(0, 49)
          return { ...t, title, thread: [...t.thread, pendingTurn] }
        })
      })()
      sessionStorage.setItem(tabsKey, JSON.stringify(withPending))
    } catch { /* ignore */ }
  }, [tabs, tabsKey, pendingSend])
  // Per-tab busy tracking — a tab is "busy" while its own ask is in flight. The
  // composer's busy/disabled state is derived from the ACTIVE tab only (see the
  // `busy` const below `activeTab`), so switching to an idle tab shows an enabled
  // composer even while another tab is still loading.
  const [busyTabs, setBusyTabs] = useState<ReadonlySet<string>>(new Set())
  // Insight keys ("briefId:insightIndex") known to already have a saved evidence
  // brief — flips the chat's first action to "View Evidence" (else it offers the
  // PRD). Populated per active insight via loadEvidenceByInsight (see effect below).
  const [insightsWithEvidence, setInsightsWithEvidence] = useState<ReadonlySet<string>>(new Set())
  const checkedEvidenceRef = useRef<Set<string>>(new Set())
  // Composer busy/disabled + "thinking" indicator reflect ONLY the active tab's
  // in-flight status. Another tab being mid-ask must not disable this composer.
  const busy = isComposerBusy(busyTabs, activeTabId)
  // Next-prompt suggestions, per tab. Fetched AFTER an answer settles, off the
  // answer path entirely: a slow, failed or never-returned request costs the
  // user nothing because the absence of suggestions is a normal, invisible
  // state (see components/shared/NextPromptSuggestions). Keyed by tab so a
  // background tab's suggestions never appear under another tab's thread, and
  // cleared the moment that tab sends again — stale chips proposing the
  // conversation the user has already moved past are worse than none.
  // Next-prompt suggestions are owned by the shared host hook (state + retire +
  // fetch-after-settle); ChatScreen keys by tab id and injects the main fetch.
  const nextPrompts = useNextPrompts(MAIN_NEXT_PROMPTS_ADAPTER)
  // Goal Analysis mode. A MODE rather than a skill: the next message starts a
  // run instead of posting a turn, so it cannot ride on `pinnedSkill` (which
  // splices a slash trigger into the query and sends it to the ask path).
  //
  // The slash-palette / pinned-skill / plus-menu / busy-hint composer state main
  // declared alongside this in its bespoke engine is owned in this branch by the
  // shared composer + engine (see the `useComposer` and `useConversation`
  // destructures above); `quote` / `editingTurnId` / `copiedTurnId` are already
  // declared by this host below. Only Goal Analysis is a main-wrapper concern, so
  // only its two fields are re-homed here.
  const [goalMode, setGoalMode] = useState(false)
  // The run currently on screen, readable without making it an effect
  // dependency — the restore below has to know whether the user started one
  // while its request was in flight, and depending on `content` would re-fire
  // the restore on every unrelated content change.
  const goalRunRef = useRef<number | null>(null)
  // Tabs whose analysis has already been PUT on screen once by the restore.
  //
  // Mirrors `reportsAutoOpenedRef`: opening once per tab is what separates
  // "you can get back to your analysis" from "a panel you closed keeps coming
  // back every time you return to this thread". Keyed by tab, not by run,
  // because two tabs can share one conversation.
  const goalAutoOpenedRef = useRef<Set<string>>(new Set())
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
  // The attachment whose content is open in the viewer overlay (click a file
  // card on a user turn). Null = closed.
  const [viewerAttachment, setViewerAttachment] = useState<{ name: string; content: string; key?: string | null; mime?: string | null; plain?: boolean } | null>(null)
  // A passage of an answer the reader highlighted and pressed Reply on, parked
  // above the input until they send or dismiss it. Host state (not the shell's)
  // because main renders its own composer; the shell reports the selection
  // through `onQuoteSelection` and owns nothing else about it. Appended to the
  // sent message as a trailing blockquote at send time (see useConversation's
  // handleComposerSubmit — the quote is passed into the engine as `quote`).
  const [quote, setQuote] = useState<string | null>(null)
  // The turn whose question is currently being rewritten in place. Exactly one
  // at a time. Cleared on save, on cancel, and whenever the active tab changes
  // (an editor left open on a tab you navigated away from would come back
  // seated over a different thread's turn).
  const [editingTurnId, setEditingTurnId] = useState<string | null>(null)
  // The turn whose copy button is showing its transient "Copied" tick.
  const [copiedTurnId, setCopiedTurnId] = useState<string | null>(null)
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Per-tab in-flight guard — keyed by tabId. Prevents a tab from firing a second
  // ask while its own is still in flight, while letting OTHER tabs send concurrently.
  const askingTabsRef = useRef<Set<string>>(new Set())
  // Per-tab STOP flag — a tab id is present while the user has stopped its
  // in-flight ask. The ask poller reads this (isStopped) to bail; it's cleared
  // when a fresh ask starts on that tab so a stop never leaks into the next ask.
  const stoppedTabsRef = useRef<Set<string>>(new Set())

  // Per-conversation thread-viewport scroll (pinned-follow, send/new-turn/tab
  // auto-jump, ResizeObserver) — extracted verbatim into the shared unit; the
  // effects live in the hook so identical scroll behaviour drives every surface.
  const { threadScrollRef, handleThreadScroll, setThreadContentEl } = useThreadScroll({
    thread,
    activeTabId,
    pendingSend,
  })

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
            // the other kinds get would claim an interruption, which is not
            // what happened here.
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
          // Same rule, same reason. A run belongs to the thread that started
          // it, and leaving it set showed thread A's analysis — with a LIVE
          // Confirm button — on thread B, where confirming would lock a goal
          // definition against a conversation the reader was not looking at.
          goalRunId: null,
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

  // Open the report a CHAT TURN is about, from its title — the shared
  // `matchReportByTitle` (exact → lenient → prefix) over this surface's thread
  // report list, with main's own panel wiring around it. No match means capture
  // hasn't landed yet (or the row is gone): open the tab and let it show what it
  // has, rather than pointing at a report that isn't there.
  const openReportByTitle = useCallback((title: string) => {
    const match = matchReportByTitle(threadReports, title)
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

  // A passage highlighted in the Document panel lands in THIS thread's
  // composer, quoted, with the caret after it — the "it comes up in the chat
  // text field" half of the requirement. The user then types the question or
  // the edit they want; nothing is sent for them.
  //
  // THE EXCERPT IS THE GROUNDING. It goes into the message itself rather than
  // riding as hidden context, which means the answer is grounded on exactly
  // the words the user pointed at, and the thread still reads honestly later:
  // anyone scrolling back sees what was being discussed instead of a question
  // about "this" with no referent.
  //
  // It PARKS as the composer's quote chip rather than being pasted into the
  // draft as raw "> " text (changed 2026-08-17, when highlight-to-reply gave
  // the surface a real quote affordance). Three things were wrong with the
  // draft-injection form and all three are gone:
  //   * the reader saw blockquote markers they never typed, in the box and
  //     again on the sent turn, and was responsible for not breaking them;
  //   * a LEADING "> " made the query's first token ">", which silently
  //     defeated `skillForQuery` and the backend's slash fast-path for any
  //     message that also pinned a skill; and
  //   * a long passage filled the composer, pushing the question out of sight.
  // The excerpt still travels INSIDE the message (as the trailing blockquote
  // `buildQuotedMessage` writes at send time), so the grounding doctrine above
  // is unchanged — only where the user sees it before sending has moved.
  //
  // A half-written question survives absolutely: the draft is not touched at
  // all now, where the old form appended to it.
  useEffect(() => {
    if (pendingDocumentQuote == null) return
    const { documentId: from, excerpt } = pendingDocumentQuote
    setPendingDocumentQuote(null)
    if (!excerpt.trim()) return
    // The quote names the document it was lifted from, and this is where that
    // is checked. Without a check the id is dead weight; with one, a passage
    // can never be quoted into a thread whose panel has since moved to a
    // different document — the panel and the composer are updated by separate
    // paths, so "the open document" is not automatically the one the user
    // highlighted.
    if (content.documentId != null && from !== content.documentId) return
    setQuote(normalizeQuote(excerpt))
    composerRef.current?.focus()
  }, [pendingDocumentQuote, setPendingDocumentQuote, content.documentId, composerRef])

  // (The layout effect that used to re-measure the composer after a quote was
  // pasted into the draft is gone with the pasting: a parked chip changes the
  // draft not at all, so there is no height to recover and no caret to rescue
  // from inside a quotation.)

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
        turns: { id?: number; role: string; content: string; attachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | null }[],
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
              // Carried through so a rehydrated thread can still be rewound to
              // this turn (edit/retry on a past prompt) — the persistence
              // layer's own id map only covers turns THIS session sent.
              ...(typeof t.id === "number" ? { dbTurnId: t.id } : {}),
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
    // Shared clarified-generation flow (trim + ack + synchronous seed + async
    // generate→bind→resume→dispatch). This surface injects its tab-scoped seams;
    // the sequence itself lives in `clarifiedGeneration` so it can't drift from
    // the project surface. The comments on each seam mark what USED to be inline.
    runSharedClarifiedGeneration(rawTask, sourceDocs, userMessage, {
      newId: () =>
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`,
      // Seed the ack turn onto THIS command tab, clear the parked clarify, flip
      // prdGenerating — one setTabs so the card→record flip and the ack land
      // together. The ack lands on a LATER turn of an existing command tab, so
      // the PRD card sits further up the thread (it anchors to thread[0]) —
      // neither "above" nor "below" is reliably true here, so it points at the chat.
      seedAckTurn: (id, message, ack) =>
        setTabs((prev) => prev.map((t) => t.id === targetTabId
          ? {
              ...t,
              pendingClarify: undefined,
              prdGenerating: true,
              thread: [...t.thread, { id, query: message, reply: ack }],
            }
          : t)),
      // The rail was deliberately NOT opened while the questions were pending
      // (see `clarifyFirst` in openPrdInTab), so answering them is what opens
      // it — otherwise the generation would run with no panel to land in. Only
      // when this is the active tab (background tabs don't touch the panel).
      openPanel: () => {
        if (targetTabId === activeTabIdRef.current) {
          setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
          setPrdPanelPending("prd")
        }
      },
      pushPendingConversation: (id, message) => pushPendingConversation(id, message, targetTabId),
      finalizeAck: (id, ack) => finalizeConversationTurn(id, { reply: ack }, targetTabId),
      onPartial: (html) => {
        if (activeTabIdRef.current === targetTabId) setContent({ prdPartialHtml: html })
      },
      // Same durable binding as the command flows, and free here: the tab has
      // been chatting (the clarifying questions landed in it), so its
      // conversation already exists and the id is a synchronous read — no
      // round-trip in front of the user's generation.
      resolveKnownConvId: () => tabsRef.current.find((t) => t.id === targetTabId)?.dbConvId ?? null,
      generateFromTask: (task, docs, knownConvId) =>
        prdApi.generateFromTask(task, false, docs, knownConvId),
      onStarted: (start, knownConvId) => {
        if (knownConvId == null) void bindConvToPrd(targetTabId, start.prd_id)
        setTabs((prev) => prev.map((t) => t.id === targetTabId
          ? { ...t, prdId: start.prd_id, title: start.title ? `PRD · ${start.title}` : t.title }
          : t))
      },
      onSuccess: (start, result) => {
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
      },
      onFailure: (message) => {
        setTabs((prev) => prev.map((t) => t.id === targetTabId ? { ...t, prdGenerating: false } : t))
        if (activeTabIdRef.current === targetTabId) setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("PRD unavailable", message.slice(0, 200))
      },
      onError: (e) => {
        setTabs((prev) => prev.map((t) => t.id === targetTabId ? { ...t, prdGenerating: false } : t))
        if (activeTabIdRef.current === targetTabId) setContent({ prdGenerating: false, prdPartialHtml: null })
        showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      },
    })
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
  // Main's `ActionConfig.onArtifactUpdated` — apply a freshly-edited PRD to the
  // captured tab + (if it's active) the ContentPanel. The panel-apply that used
  // to live inline in `prdChatEditFlow`.
  const applyPrdArtifactInTab = useCallback(
    (tabId: string, update: { kind: "prd"; prdId: number; record: PrdRecord }) => {
      // Drop stale local drafts so the panel shows the server copy.
      clearPrdDrafts(update.prdId)
      const prd = prdStateFromRecord(update.record)
      setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, prd } : t)))
      if (tabId === activeTabIdRef.current) setContent({ prd })
    },
    [setContent],
  )

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
  // (The ticket-assignment flow moved into the shared action layer —
  // `runAssignTicketsAction`, config'd by `onAssignTickets` with main's captured
  // tab + the dock assign question. `completeAssign`/`cancelAssign` below still
  // apply the popup's answers, unchanged, until main adopts the engine.)

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
  ): SlackShareTargetRef => resolveShareRef(envelope, {
    prdId: tab?.prdId ?? null,
    ticketSetId: tab?.ticketSetId ?? null,
    reportId,
  }), [])

  // (The share-to-Slack PREVIEW flow moved into the shared action layer —
  // `runShareToSlackAction`, config'd by `onShareToSlack` with main's tab/report
  // share-ref + the dock channel picker. The interactive card handlers below —
  // send / re-preview / question — come from the shared
  // `useSlackShareCardHandlers`; main injects seams bound to the ACTIVE tab, the
  // only tab a share card is ever visible on: `patchTurn`/`getShare` reach the
  // active tab's turn, `getPendingShare`/`setPendingShare` its dock question.)
  const slackPatchTurn = useCallback(
    (turnId: string, patch: Partial<NonNullable<ThreadTurn["slackShare"]>>) =>
      patchSlackShare(activeTabIdRef.current ?? "", turnId, patch),
    [patchSlackShare],
  )
  const slackGetShare = useCallback(
    (turnId: string) =>
      tabsRef.current.find((t) => t.id === activeTabIdRef.current)
        ?.thread.find((tn) => tn.id === turnId)?.slackShare,
    [],
  )
  const slackGetPendingShare = useCallback(
    () => tabsRef.current.find((t) => t.id === activeTabIdRef.current)?.pendingShare,
    [],
  )
  const slackSetPendingShare = useCallback(
    (ps: PendingShareState | undefined) =>
      setTabs((prev) => prev.map((t) =>
        t.id === activeTabIdRef.current ? { ...t, pendingShare: ps } : t)),
    [],
  )
  const { sendSlackShare, repreviewSlackShare, completeShareQuestion, cancelShareQuestion } =
    useSlackShareCardHandlers({
      patchTurn: slackPatchTurn,
      getShare: slackGetShare,
      getPendingShare: slackGetPendingShare,
      setPendingShare: slackSetPendingShare,
    })

  /** The one place anything is actually posted to Slack. */
  // The assign batch's ONE landing (the popup collected every pick, then these
  // writes happen through the ordinary fields endpoint, summary as its own agent
  // turn) comes from the shared `useAssignCompletion` — the project host calls
  // the SAME unit. Main injects seams bound to the ACTIVE tab, the only tab an
  // assign popup is ever open on: read/clear its `pendingAssign`, toggle its
  // busy set, append the summary turn (main's crypto-id), finalize it.
  const assignGetPending = useCallback(
    () => tabsRef.current.find((t) => t.id === activeTabIdRef.current)?.pendingAssign,
    [],
  )
  const assignClearPending = useCallback(
    () => setTabs((prev) => prev.map((t) =>
      t.id === activeTabIdRef.current ? { ...t, pendingAssign: undefined } : t)),
    [],
  )
  const assignSetBusy = useCallback((busy: boolean) => {
    const tabId = activeTabIdRef.current ?? ""
    setBusyTabs((prev) => busy ? addToSet(prev, tabId) : removeFromSet(prev, tabId))
  }, [])
  const assignAppendReplyTurn = useCallback((reply: AskResponse) => {
    const noteId =
      typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    setTabs((prev) => prev.map((t) => t.id === activeTabIdRef.current
      ? { ...t, thread: [...t.thread, { id: noteId, query: "", reply }] }
      : t))
  }, [])
  const assignFinalizeTurn = useCallback(
    (turnId: string, reply: AskResponse) =>
      finalizeConversationTurn(turnId, { reply }, activeTabIdRef.current ?? ""),
    [finalizeConversationTurn],
  )
  const { completeAssign, cancelAssign } = useAssignCompletion({
    getPendingAssign: assignGetPending,
    clearPendingAssign: assignClearPending,
    setBusy: assignSetBusy,
    appendReplyTurn: assignAppendReplyTurn,
    finalizeTurn: assignFinalizeTurn,
  })

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

  // ── The generation-turn SEED seam (tab-orchestrator, main-provided) ─────────
  // The shared entry every command-generation flow uses to land its settled
  // acknowledgement turn: reuse the active tab if it's a plain chat (never a
  // PRD/insight-bound one, which `reusableActiveTab` declines so a generation
  // can't hijack a bound tab), else spawn a fresh chat tab; rename a placeholder,
  // append the turn, clear the composer, and persist rail+Supabase. Returns the
  // resolved conversation key + its bound DB conversation id (null on a fresh
  // tab). This is the EXACT block the flows used to inline — kept verbatim so
  // main is byte-identical; it is injected into the generation flows as a seam so
  // a project slot provides its own single-conversation version at wiring time.
  const seedGenerationTurn = useCallback((seedTurn: ThreadTurn): { tabId: string; dbConvId: number | null } => {
    const inTab = reusableActiveTab()
    const seedQuery = seedTurn.query
    const handle = seedQuery.length > 40 ? `${seedQuery.slice(0, 37)}…` : seedQuery
    let tabId: string
    let dbConvId: number | null = null
    if (inTab) {
      tabId = inTab.id
      dbConvId = inTab.dbConvId ?? null
      setTabs((prev) => prev.map((t) => t.id === inTab.id
        ? {
            // First message in a placeholder "New chat" tab → take the real
            // title from the command, exactly as submitAsk's own rename does.
            ...t,
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
    pushPendingConversation(seedTurn.id, seedQuery, tabId)
    void finalizeConversationTurn(seedTurn.id, seedTurn.reply ? { reply: seedTurn.reply } : {}, tabId)
    return { tabId, dbConvId }
  }, [reusableActiveTab, openTab, pushPendingConversation, finalizeConversationTurn])

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

  /** Tabs whose DOCUMENT has been auto-opened on this visit. Same claim, same
   *  retirement point as its two siblings above — leaving a tab retires it, so
   *  coming back opens the document again. Declared here rather than beside its
   *  effect so the tab-switch reconcile (which retires it) reads in order. */
  const documentAutoOpenedRef = useRef<Set<string>>(new Set())

  /** Kick off ONE run for `tabId` and own its whole lifecycle on the tab.
   *
   *  The latch is written on the same commit as the kick-off, before any await,
   *  because that is the window the double-generation guard has to cover: two
   *  sends a second apart both read `ticketSetRunning` before either had come
   *  back from the network. */
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
    (candidate: OpenArtifactCandidate, seedQuery?: string): boolean =>
      // The evidence-vs-PRD branch, resume-conversation-first, reuse-by-prd-id
      // and null-id guards are the shared `openArtifactDestination` decision;
      // ChatScreen supplies the PANEL terminal actions (its exact current
      // bodies) as the adapter, so the routing is byte-identical. Project
      // surfaces open the artifacts MODAL instead — the sanctioned, ledgered
      // `open.destination` divergence — and do NOT route through this decision.
      openArtifactDestination(
        candidate,
        {
          openEvidence: (c, sq) => {
            // The SAME binding guard the PRD branch uses. Pinning the active tab
            // unconditionally let an evidence open hijack a tab already holding a
            // PRD: openPrdInTab's evidence branch writes `evidenceOnly` +
            // `evidenceDetail` for insight B onto a tab whose prdId is still A, so
            // the panel renders B's evidence beside A's document and the tab is
            // flagged evidence-only while holding a prd id. `reusableActiveTab`
            // declines exactly that tab, and the open gets a chat of its own.
            const inTab = reusableActiveTab()
            const req: LocalPrdTabRequest = {
              title: c.title || "Evidence",
              ...(sq ? { seedQuery: sq } : {}),
              ...(inTab ? { inTabId: inTab.id } : {}),
              source: {
                kind: "evidence",
                meta: { briefId: c.brief_id!, insightIndex: c.insight_index! },
                detail: null,
              },
            }
            const tabId = openPrdInTab(req)
            seedCommandTurn(req, tabId)
            return true
          },
          resumeConversation: ({ conversationId, conversationTitle, prdId }) => {
            // The PRD's own THREAD outranks a panel-beside-this-chat open (owner
            // decision, 2026-08-14): when the conversation that produced the
            // document survives, "open the PRD" means going back to that chat —
            // history restored, PRD panel over it — exactly like clicking the same
            // row on the Artifacts screen. Storage unavailable → false, and the
            // decision falls through to the panel-only open.
            try {
              localStorage.setItem("sprntly_resume_conv", JSON.stringify({
                dbId: conversationId,
                title: conversationTitle,
                fallbackTurns: [],
                prdId,
              }))
              checkResume()
              return true
            } catch {
              return false
            }
          },
          openPrd: (c, prdId, sq) => {
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
              title: c.title ? `PRD · ${c.title}` : "PRD",
              ...(sq ? { seedQuery: sq } : {}),
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
                  c.brief_anchored &&
                  c.brief_id != null &&
                  c.insight_index != null
                    ? { briefId: c.brief_id, insightIndex: c.insight_index }
                    : null,
              },
            }
            const tabId = openPrdInTab(req)
            seedCommandTurn(req, tabId)
            return true
          },
        },
        seedQuery,
      ),
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
  // Main's `ActionConfig.emitTurn` — the ONE surface-specific primitive the
  // shared action layer needs from main: place a fully-formed, settled command
  // turn into the active tab (append / rename) or a fresh tab, then persist it
  // client+server. A project surface supplies its own `emitTurn` (engine turns +
  // server-only persist); the action bodies never learn which.
  const emitCommandTurn = useCallback((turn: ThreadTurn, intoTabId?: string) => {
    const seedQuery = turn.query
    const handle = seedQuery.length > 40 ? `${seedQuery.slice(0, 37)}…` : seedQuery
    // `intoTabId` PINS THE DESTINATION. Without it this lands on whatever tab is
    // active when it runs — and a Goal Analysis plan turn is emitted minutes
    // after its gate was answered, so a reader who switched tabs meanwhile got
    // another conversation's plan grafted into their thread.
    const activeId = intoTabId ?? activeTabIdRef.current
    const inTab = activeId && activeId !== BRIEF_TAB_ID
      ? tabsRef.current.find((t) => t.id === activeId)
      : undefined
    // A NAMED tab that no longer exists is a DROPPED turn, never a new one.
    // Pinning the destination made the id load-bearing without making a stale
    // one detectable: falling through to `openTab(handle, …)` with an empty
    // `handle` — every gate turn carries `query: ""` — spawned a blank-titled
    // tab in the rail holding a plan for a conversation the reader had closed.
    if (intoTabId && !inTab) return
    let tabId: string
    if (inTab) {
      tabId = inTab.id
      setTabs((prev) => prev.map((t) => t.id === inTab.id
        ? {
            ...t,
            title: t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? handle : t.title,
            thread: [...t.thread, turn],
          }
        : t))
      setDraft("")
    } else {
      tabId = openTab(handle, [turn])
    }
    // ONLY A TURN WITH TEXT IS A CONVERSATION TURN. The gate turns carry
    // `query: ""` — they are a card, not something the user said — and pushing
    // them persisted an empty user row per gate into the thread's history,
    // which then replays as a blank message on every later restore.
    if (seedQuery) pushPendingConversation(turn.id, seedQuery, tabId)
    if (turn.reply) void finalizeConversationTurn(turn.id, { reply: turn.reply }, tabId)
  }, [openTab, pushPendingConversation, finalizeConversationTurn])

  // ── Resolve the tab a send lands on (main's tab multiplexer) ──────────────
  // Tab spawn/route is a WRAPPER concern: no active tab (or the synthetic brief
  // tab) spawns a FRESH chat tab seeded with the turn; otherwise the turn appends
  // to — and, on a placeholder "New chat", renames — the active tab. Returns the
  // resolved target plus the rollback anchors an extraction failure needs. The
  // single-conversation run in the engine is surface-agnostic and never spawns
  // tabs; it calls this seam.
  const resolveSendTarget = useCallback(
    (
      newTurn: ThreadTurn,
      handle: string,
    ): {
      targetTabId: string
      spawnedNewTab: boolean
      prevActiveTabId: string | null
      prevTitle: string | null
    } => {
      const prevActiveTabId = activeTabId
      const spawnedNewTab = !activeTabId || activeTabId === BRIEF_TAB_ID
      const prevTitle = spawnedNewTab
        ? null
        : tabsRef.current.find((t) => t.id === activeTabId)?.title ?? null
      let targetTabId: string
      if (spawnedNewTab) {
        const title = handle.length > 40 ? `${handle.slice(0, 37)}…` : handle
        targetTabId = openTab(title, [newTurn])
      } else {
        // spawnedNewTab === false guarantees a non-empty activeTabId.
        targetTabId = activeTabId!
        const newTitle = handle.length > 40 ? `${handle.slice(0, 37)}…` : handle
        setTabs((prev) => prev.map((t) => {
          if (t.id !== targetTabId) return t
          // First message in a placeholder "New chat" tab → give it the real
          // title from the query (rename in place; do NOT spawn a second tab).
          const title = t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? newTitle : t.title
          return { ...t, title, thread: [...t.thread, newTurn] }
        }))
      }
      return { targetTabId, spawnedNewTab, prevActiveTabId, prevTitle }
    },
    [activeTabId, openTab],
  )

  // ── The prd-thinking guard + clarify-first intercept (wrapper seam) ────────
  // A send landing INSIDE a deferred PRD-ack window (a PRD command's reply is
  // deferred — see deferredAckRef) or a parked sufficiency gate is intercepted
  // before intent classification: the first would invert the persisted
  // user→assistant pairing the history restore depends on; the second must be
  // read as the gate's ANSWERS, never a fresh command. Injected into the engine's
  // submit; folds into the engine when the clarify machinery does. Returns true
  // when it handled (and settled) the send.
  const interceptBeforeIntent = useCallback(
    async ({ rawQuery, trimmed, docFile, activeTab, settlePendingSend }: {
      rawQuery: string
      trimmed: string
      docFile: File | null
      activeTab: ChatTab | undefined
      settlePendingSend: () => void
    }): Promise<boolean> => {
      if (activeTab?.prdCommandThinking) {
        settlePendingSend()
        setDraft(rawQuery)
        showToast("One moment", "Still working out that PRD request — I'll take your next message in a second.")
        return true
      }
      // Clarify-first answers: the tab's PRD task is parked behind the sufficiency
      // gate — the message IS the answers (or a "generate now" skip). Resolves as
      // "chat" (or "skip" for a bare "generate now") since free text has no
      // per-question mapping, and settles the batch so the record matches the card.
      if (activeTab?.pendingClarify && !docFile) {
        const { task, sourceDocs, turnId } = activeTab.pendingClarify
        const skipped = CLARIFY_SKIP_RE.test(trimmed)
        const combined = skipped
          ? task
          : `${task}\n\nAdditional details from the user:\n${trimmed}`
        markClarifyResolved(activeTab.id, turnId, { answers: [], mode: skipped ? "skip" : "chat" })
        const { prdApi } = await import("../../../lib/api")
        runClarifiedGeneration(prdApi, activeTab.id, combined, sourceDocs, trimmed)
        settlePendingSend()
        return true
      }
      return false
    },
    [setDraft, showToast, markClarifyResolved, runClarifiedGeneration],
  )
  // Holds `startGoalAnalysis` for `useConversation`, which is called several
  // hundred lines ABOVE that function's declaration. Kept in a ref rather than
  // reordering the hook setup, and republished on every render (below) so the
  // dispatcher always calls the current closure rather than the first one.
  const startGoalAnalysisRef =
    useRef<
      ((goalText: string, saidText?: string) => void | Promise<void>) | null
    >(null)


  // ── The per-conversation store seam ───────────────────────────────────────
  // The single-conversation engine, extracted into the shared `useConversation`.
  // Main injects its exact tab machinery + the grounding seam + the generation +
  // submit leaf seams, so the run, the artifact-generation flows, and the send
  // pipeline stay byte-unchanged; the engine builds `makeHandle` /
  // `resolveAskParams` / `getPrdId` internally, owns the ask-core +
  // `useConversationGeneration` + `submitAsk`, and returns the run/stop/
  // action-turn functions, the generation flows, and `submitAsk`. composer /
  // clarify fold in next.
  const {
    runConversationAsk, handleStopAsk, runActionTurnInTab, submitAsk,
    handleComposerSubmit, handleComposerKeyDown,
    listArtifactsFlow, prdChangeTemplateFlow, ticketsChangeTemplateFlow, documentCommandFlow,
    openArtifactFlow, ticketSetCommandFlow, handleTicketSetAction,
  } = useConversation({
      // A REF, because `startGoalAnalysis` is declared several hundred lines
      // below this call and a direct reference is a use-before-declaration.
      //
      // The slot must exist unconditionally and must ACT, because the
      // dispatcher decides `handled` from presence alone (its peek pass stubs
      // every body to a no-op, so a return value cannot be read). The ref is
      // written during the same render that declares the callback, above any
      // interaction, so it is never null when this runs.
      // BOTH ARGUMENTS. This shim dropped `saidText` on the floor, which made
      // the whole "show what the reader typed" fix inert — the thread went on
      // rendering the planner's extraction and every test still passed,
      // because the only path a test drives (the `+` menu) sends one argument
      // anyway. A forwarder that silently narrows its own signature is the
      // same shape of bug as an intent missing from `_CLIENT_INTENTS`.
      startGoalAnalysis: (goalText: string, saidText?: string) => {
        startGoalAnalysisRef.current?.(goalText, saidText)
      },
      tabsRef,
      activeTabId,
      activeTabIdRef,
      setTabs,
      setBusyTabs,
      askingTabsRef,
      stoppedTabsRef,
      activeCompany,
      persistence,
      mountedRef,
      animatedTurnIds,
      askStartRef,
      resumedTurnsRef,
      pushPendingConversation,
      setActiveConv,
      finalizeConversationTurn,
      emitCommandTurn,
      seedGenerationTurn,
      threadContextFor,
      openArtifactInPanel,
      postOpenArtifactReply,
      markTicketSetAutoOpened: (key) => { ticketSetAutoOpenedRef.current.add(key) },
      postSummary: (key, kind, artifactId) => { postSummaryRef.current?.(key, kind, artifactId) },
      setContent,
      openContentPanel,
      content,
      composer,
      busy,
      viewerAttachmentOpen: Boolean(viewerAttachment),
      setActiveTabId,
      resolveSendTarget,
      interceptBeforeIntent,
      importPrdCommandFlow,
      prdCommandFlow,
      applyPrdArtifactInTab,
      shareRefFor,
      nextPrompts,
      showToast,
      // Highlight-to-reply: the parked quote rides the next composer send as a
      // trailing blockquote. Host state (main renders its own composer); the
      // engine's submit appends it and calls onQuoteConsumed to clear it.
      quote,
      onQuoteConsumed: () => setQuote(null),
    })


  // "Ask again" on a stopped / timed-out / failed turn — the surface used to be
  // a dead end at all three.
  //
  // Attachments are NOT re-sent: their bytes left component state on the
  // original send, and quietly re-asking the same words WITHOUT the files the
  // user attached is a different question. So a turn that carried files hands
  // its text back to the composer instead, which is also what the failure copy
  // ("try it with fewer files attached") tells the reader to do.
  const handleAskAgain = useCallback((turn: ThreadTurn) => {
    askAgain(turn, { submit: submitAsk, setDraft, composerRef })
  }, [submitAsk])

  // ── Highlight-to-reply ────────────────────────────────────────────────────
  // A passage the reader selected in an answer, handed up by the shell's
  // selection toolbar. It parks above the input and rides the next message as a
  // trailing blockquote (appended in the engine's handleComposerSubmit via the
  // `quote` seam) — quoting is a way to point at a sentence, not a second send
  // button, so nothing is dispatched here.
  //
  // The focus is DEFERRED A FRAME, and that is the whole of it: this runs
  // inside the toolbar button's click handler, which is not a safe moment to
  // put a caret anywhere. React has not committed `setQuote` yet, so the
  // composer on screen is not the one being focused; and the caller's very
  // next statement is `window.getSelection().removeAllRanges()` — clearing the
  // document selection immediately after a focus, which on the engines that
  // model an input's caret as part of that selection takes the caret straight
  // back out. Either way the field looked focused and swallowed the first
  // thing typed into it. `focusComposerNextFrame` runs after the click handler
  // has finished and after the commit, which is the same moment the document
  // quote's own focus already happens from (it focuses from an effect, which
  // is why THAT path never had this bug).
  const handleQuoteSelection = useCallback((text: string) => {
    setQuote(text)
    focusComposerNextFrame()
  }, [focusComposerNextFrame])

  // ── Copy a past prompt ────────────────────────────────────────────────────
  // The WORDS, not the wire form: a message that quoted a passage stores it as
  // a trailing blockquote, and pasting "> …" markup into wherever you are
  // taking this is never what was meant. The excerpt has its own affordance
  // (the quote block opens in the viewer).
  const handleCopyTurn = useCallback((turn: ThreadTurn) => {
    const body = splitQuotedSuffix(turn.query).body || turn.query
    if (!body) return
    void (async () => {
      try {
        await navigator.clipboard.writeText(body)
        setCopiedTurnId(turn.id)
        if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
        copiedTimerRef.current = setTimeout(() => setCopiedTurnId(null), COPIED_HINT_MS)
      } catch {
        // Denied permission, an insecure origin, a browser without the API —
        // say so rather than leaving a button that silently does nothing.
        showToast("Couldn't copy", "Your browser blocked clipboard access — select the text and copy it manually.")
      }
    })()
  }, [showToast])

  // ── Re-ask a past prompt (edit / retry) ───────────────────────────────────
  // Both rewind the conversation to that turn: it and everything after it are
  // replaced by the new question and its answer. That is the only coherent
  // meaning for editing something already answered — leaving the old reply
  // underneath a rewritten question would make the thread a record of a
  // conversation that never happened.
  //
  // The rewind is TWO things that must agree, which is the whole reason this is
  // one shared helper rather than two similar ones:
  //   * the thread on screen, truncated at `turn`; and
  //   * the persisted conversation, rewound to the same point
  //     (`rewindToUserTurn` → DELETE …/turns/{id}, which drops that row and
  //     every row after it).
  // Queued so the server sees rewind-then-add; best-effort, so a failure leaves
  // the record longer than the screen rather than breaking the send.
  const reAskFromTurn = useCallback((turn: ThreadTurn, nextQuery: string) => {
    const tabId = activeTabId
    if (!tabId || !nextQuery.trim()) return
    setTabs((prev) => prev.map((t) => {
      if (t.id !== tabId) return t
      const idx = t.thread.findIndex((x) => x.id === turn.id)
      // Not found means the thread moved under us (a background answer landed,
      // the tab was rehydrated) — drop the whole thing rather than truncate at
      // a guess. The user can act on the turn again.
      return idx === -1 ? t : { ...t, thread: t.thread.slice(0, idx) }
    }))
    void persistence.rewindToUserTurn(tabId, turn.id, turn.dbTurnId)
    void submitAsk(nextQuery)
  }, [activeTabId, persistence, submitAsk, setTabs])

  const handleRetryTurn = useCallback((turn: ThreadTurn) => {
    // Verbatim — including the quoted passage, which is part of the question
    // that is being asked again.
    reAskFromTurn(turn, turn.query.trim())
  }, [reAskFromTurn])

  const handleEditTurn = useCallback((turnId: string) => {
    setEditingTurnId(turnId)
  }, [])
  const handleCancelTurnEdit = useCallback(() => setEditingTurnId(null), [])

  const handleSubmitTurnEdit = useCallback((turn: ThreadTurn, text: string) => {
    setEditingTurnId(null)
    const body = text.trim()
    if (body.length < DRAFT_MIN_CHARS) return
    // The passage the original message was replying to is kept: the editor owns
    // your words, not the excerpt they were about. Re-composed the same way the
    // composer would have.
    const { quote: repliedTo } = splitQuotedSuffix(turn.query)
    const next = buildQuotedMessage(body, repliedTo)
    // Saving without changing anything closes the editor and stops. Re-running
    // an identical question is what Retry is for, and doing it silently here
    // would spend a whole generation on a keystroke the user took back.
    if (next === turn.query) return
    reAskFromTurn(turn, next)
  }, [reAskFromTurn])

  // An editor left open on a tab the user navigated away from would come back
  // seated over whatever turn happens to be there now. Close it on any tab
  // change — the thread it belonged to is no longer the one on screen. The
  // "Copied" tick goes with it for the same reason.
  useEffect(() => {
    setEditingTurnId(null)
    setCopiedTurnId(null)
  }, [activeTabId])
  useEffect(() => () => {
    if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
  }, [])

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
      // Same claim, same retirement, for a thread whose artifact is a DOCUMENT.
      // Without this the probe fires once per session: leaving a document
      // thread closes the panel and the thread-reset clears `documentId`, so
      // coming back would land in exactly the state the probe exists to fix —
      // panel shut, document reachable only from the library — and a
      // `generating` document would lose its live view for the rest of the
      // session.
      documentAutoOpenedRef.current.delete(prevTabForPanelRef.current)
      // Same claim, same retirement, for a thread whose artifact is a GOAL
      // ANALYSIS — and for the identical reason the document comment gives.
      // The reconcile below closes the panel on the way out, so a claim that
      // outlives the visit means the restore declines on return: slot set,
      // panel shut, and `ContentPanel` only un-hides the `goal` tab once the
      // panel is open. That is #1283 again, one tab switch later.
      goalAutoOpenedRef.current.delete(prevTabForPanelRef.current)
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

  // ── Document → its own thread hand-off ────────────────────────────────────
  // Clicking a team document in Artifacts writes the ordinary
  // `sprntly_resume_conv` hand-off and fills `pendingDocumentFocus`.
  // Structurally the report hand-off above, one artifact over: gated on the
  // conversation id matching, so a failed resume opens nothing rather than the
  // wrong thread's document.
  //
  // A document used to open its own PAGE from Artifacts instead — the reasoning
  // being that writing wants the full measure of a page. It still does, and the
  // page is still there; what was wrong is that this row behaved differently
  // from every other row in the same list. A document born in a chat opens over
  // that chat, like the PRD, the report and the ticket set beside it. One with
  // no chat behind it (uploaded, or its thread deleted) still opens the page.
  useEffect(() => {
    if (!pendingDocumentFocus) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.dbConvId !== pendingDocumentFocus.conversationId) return
    const documentId = pendingDocumentFocus.documentId
    setPendingDocumentFocus(null)
    setContent({ documentId, documentGenerating: false })
    openContentPanel("document")
  }, [
    pendingDocumentFocus, setPendingDocumentFocus, activeTabId,
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

  // ── A thread that produced a DOCUMENT opens on it ──────────────────────────
  // The same requirement as the ticket-set probe directly above, for the one
  // artifact that never had it — now the SHARED `useDocumentReopenProbe`, which
  // both this surface and the project surface run. Main's context flows in via
  // the probe: its per-tab guards + once-per-tab marker (`begin`), its
  // `activeTabIdRef`/`contentPanelTabRef`/`contentDocumentIdRef` post-fetch
  // reads, and its "TICKETS WIN THE PANEL" late-precedence arm (`ticketsWin`) —
  // both probes await on an empty panel, so a ticket set `loadTicketSet` is
  // still filling keeps the panel and the document is one click away in the
  // strip. Deps are main's own, unchanged. A failed document still shows in the
  // library (#1184); `generating` DOES open, the live state the panel shows.
  useDocumentReopenProbe(
    {
      begin: () => {
        if (!activeTabId || isBriefTab || pendingReportFocus || pendingTicketSetFocus) return null
        if (documentAutoOpenedRef.current.has(activeTabId)) return null
        const tabId = activeTabId
        const tab = tabsRef.current.find((t) => t.id === tabId)
        if (!tab || tab.dbConvId == null) return null
        if (tab.prd || tab.prdGenerating || tab.prdId != null) return null
        documentAutoOpenedRef.current.add(tabId)
        return tab.dbConvId
      },
      stillActive: () => activeTabIdRef.current === activeTabId,
      panelOpen: () => Boolean(contentPanelTabRef.current),
      documentClaimed: () => contentDocumentIdRef.current != null,
      ticketsWin: () => tabsRef.current.find((t) => t.id === activeTabId)?.ticketSetId != null,
      setContent,
      openContentPanel,
    },
    [
      activeTabId, isBriefTab, pendingReportFocus, pendingTicketSetFocus,
      tabs, setContent, openContentPanel,
    ],
  )

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
            // Grounded progress on a re-attached generation — same curated,
            // flag-gated `livePhase` seam as the POST path. (Phase frames are not
            // replayed on a mid-generation join, so a resumed turn shows only the
            // leg that starts next; that is honest, not a gap.)
            GROUNDED_PROGRESS_ENABLED
              ? (label) => {
                  setTabs((prev) => prev.map((t) =>
                    t.id !== targetTabId ? t : {
                      ...t, thread: t.thread.map((turn) =>
                        turn.id === turnId && !turn.reply && !turn.stopped
                          ? { ...turn, livePhase: label }
                          : turn),
                    }
                  ))
                }
              : undefined,
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
                ? { ...turn, reply: res, partial: undefined, streamDropped: undefined, timedOut: undefined, livePhase: undefined }
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
                  ? { ...turn, timedOut: true, partial: undefined, streamDropped: undefined, livePhase: undefined }
                  : turn),
              }
            ))
            return
          }
          const msg = e instanceof Error ? e.message : "Something went wrong"
          setTabs((prev) => prev.map((t) =>
            t.id !== targetTabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === turnId
                ? { ...turn, error: msg, streamDropped: undefined, livePhase: undefined }
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

  // ── Goal Analysis: restore-after-reload + start (main-wrapper concerns) ─────
  // main declared its bespoke submit / slash-palette / plus-menu / skill
  // handlers here; in this branch those are owned by the shared engine
  // (`useConversation`) and composer (`useComposer`). Only Goal Analysis is
  // main-specific, so only it is re-homed into this wrapper, alongside three thin
  // host wrappers that let goal mode intercept the shared submit / Enter / `+`
  // menu without teaching the surface-agnostic engine about it.

  // Restore this thread's analysis after a reload.
  //
  // `goalRunId` lives in the shared content slot, which is memory only, so
  // without this a refresh made the run UNREACHABLE — it kept going on the
  // server, finished, and had no way back onto the screen.
  //
  // Gated on the flag, so an unenrolled company never pays a request (or
  // collects a 403) on every thread switch.
  useEffect(() => {
    // Mirrors the content reset above: the thread changed, so whatever run was
    // on screen belongs to the previous one. Clearing the ref too is what lets
    // the restore below run for the NEW thread instead of seeing a stale id
    // and declining.
    goalRunRef.current = null
    if (!goalAnalysisOn || activeConvId == null) return
    let live = true
    void (async () => {
      try {
        const { runs } = await goalAnalysisApi.list()
        if (!live) return
        // Newest first from the server; take this thread's most recent that is
        // still worth showing. A `failed` or `cancelled` run must NOT reopen
        // the panel: it would pin an undismissable red tab to that thread for
        // as long as the run row exists, with nothing the reader can do about
        // it.
        const mine = runs.find(
          (r) => r.conversation_id === activeConvId
            && r.status !== "failed" && r.status !== "cancelled",
        )
        if (!live || !mine) return
        // Never clobber a run the user started while this request was in
        // flight — the restore would yank the panel back to an older one.
        if (goalRunRef.current != null) return
        goalRunRef.current = mine.id
        setContent({ goalRunId: mine.id })

        // RE-ARM AN UNANSWERED GATE. `goalGate` lives on the thread, which
        // lives in sessionStorage — so a new browser session (or any tab whose
        // thread was not persisted) came back with the run sitting at its gate
        // and NOTHING anywhere to answer it: the panel had handed the gates to
        // the chat, and the chat had no card. The run was stuck for good.
        //
        // Rebuilt from the run itself, which is the durable copy.
        if (mine.status !== "awaiting_confirmation"
            && mine.status !== "awaiting_approval") return
        const tabId = activeTabIdRef.current
        if (!tabId) return
        const tab = tabsRef.current.find((t) => t.id === tabId)
        if (!tab) return
        // A tab whose thread is still being fetched is NOT an empty tab. Seeding
        // it here races `resumeChat`'s fill, which only writes when the thread
        // is still empty — so the loser is whichever arrives second, and the
        // gate can be silently swallowed. `reusableActiveTab` skips a hydrating
        // tab for exactly this reason; the next run of this effect catches it.
        if (tab.hydrating) return

        // KEYED ON THE RUN, NOT ON "HAS THIS THREAD EVER HELD A GATE". The first
        // version asked the latter, so the settled record of a PREVIOUS run —
        // the artefact this whole change exists to keep in the thread — blocked
        // the rebuild for the next one, and a second analysis in one chat was
        // unanswerable after a reload.
        //
        // ACROSS EVERY TAB, because two tabs can share a conversation id (this
        // effect's own dependency comment says so) and each is restored
        // independently — one gate per tab means two live Confirm buttons for
        // one question, which is the duplicate the panel was stripped to avoid.
        const alreadyOnScreen = tabsRef.current.some((t) => t.thread.some((tn) =>
          (tn.goalGate?.kind === "definition" || tn.goalGate?.kind === "plan")
            && tn.goalGate.runId === mine.id))
        if (alreadyOnScreen) return
        const detail = await goalAnalysisApi.get(mine.id)
        if (!live) return
        // APPENDS a fresh gate turn. An earlier version tried to hang the
        // rebuilt gate back on the turn that had been carrying one, to keep the
        // card next to the reader's own words. That cosmetic nicety needed a
        // marker on the turn, and the marker produced five separate Criticals
        // in one review round: unkeyed it captured another run's gate, keyed it
        // was empty at the moment it mattered, and every exit that failed to
        // clear it orphaned a permanently blank turn into sessionStorage.
        //
        // Appending is what the code already did before any of that, it has no
        // state of its own to get wrong, and nobody has ever complained that a
        // restored card came back a message lower.
        if (detail.status === "awaiting_confirmation") {
          emitCommandTurn({
            id: `goal-restored-${mine.id}`,
            query: "",
            goalGate: {
              kind: "definition",
              runId: mine.id,
              goalText: detail.goal_text ?? "",
              ask: detail.prioritisation?.ask
                || "Before this runs, confirm what this goal means.",
              proposedDefinition: detail.prioritisation?.proposed_definition,
              proposedSource: detail.prioritisation?.proposed_source,
              methodNote: detail.prioritisation?.method_note,
            },
          }, tabId)
        } else if (detail.prioritisation?.plan) {
          emitCommandTurn({
            id: `goal-plan-${mine.id}`,
            query: "",
            goalGate: {
              kind: "plan", runId: mine.id, plan: detail.prioritisation.plan,
            },
          }, tabId)
        }
      } catch {
        // A failed restore is a missing panel, not a broken chat. The run row
        // survives and the listing will be tried again on the next switch.
      }
    })()
    return () => { live = false }
    // `activeTabId` is a REAL dependency: two tabs can share one conversation
    // id, so without it this effect does not re-run on the switch — the reset
    // clears the panel, the restore never fires again, and switching back does
    // not recover it.
    // `activeTabHydrating` IS A DEPENDENCY, and it is the whole of finding 2:
    // bailing on a hydrating tab without it meant bailing forever, because
    // nothing else in this list changes when the thread fetch lands. Opening a
    // conversation with a live gate from the history rail therefore never
    // restored it. Listed here, the effect re-runs the moment hydration ends.
  }, [goalAnalysisOn, activeConvId, activeTabId, activeTabHydrating, setContent,
      emitCommandTurn, setTabsGoalGate])

  // ...AND PUT IT ON SCREEN.
  //
  // The restore above fills the slot; `ContentPanel` un-hides the `goal` tab
  // only once the panel is OPEN, and on a fresh load it is closed. Filling the
  // slot alone therefore restored the run invisibly, with no control anywhere
  // to reveal it (#1283).
  //
  // STATE-DRIVEN, not done inside the fetch, and that distinction is the whole
  // reason this is a separate effect:
  //
  //   - reading `contentPanelTabRef` mid-fetch sees the value from the render
  //     in which the reconcile called `closeContentPanel()`, so it concludes
  //     "something is open", declines, and — the fetch being over — nothing
  //     re-runs. The panel then never opens at all.
  //   - the closure's `activeTabId` can be a thread the reader has already
  //     left: `live` only flips when React flushes the cleanup, and a listing
  //     resolving inside that window would open the panel on thread A's
  //     analysis, live Confirm button and all, over thread B.
  //
  // Keyed on the state instead, both windows close: it re-evaluates whenever
  // the panel or the active tab actually changes, and `goalRunId` is already
  // reset per thread, so it can only ever open the run the slot currently
  // holds. Same shape as the reports auto-open a few hundred lines up.
  useEffect(() => {
    if (!goalAnalysisOn || isBriefTab || !activeTabId) return
    if (content.goalRunId == null) return
    // TWO SOURCES OF TRUTH, AND ONLY ONE OF THEM IS CURRENT HERE.
    //
    // On the commit where the reader switches tabs, `activeTabId` is already
    // the NEW tab while `content.goalRunId` is still the OLD thread's: the
    // per-thread reset at `:2188` is itself an effect, and passive effects in
    // one flush all close over the same render, so this runs BEFORE that
    // setContent has re-rendered. `contentPanelTab` is stale for the same
    // reason, so the hijack guard below cannot save it either.
    //
    // Left unchecked that opened `goal` on a thread with no analysis — a blank
    // 60vw panel once the reset landed — and, being the last write in the
    // commit, it overwrote a PRD the reconcile had just opened for the thread
    // the reader actually navigated to.
    //
    // `goalRunRef` is the value that IS current: the restore effect declared
    // above clears it synchronously at the top, so a mismatch means the slot
    // still holds the previous thread's run and this flush has no business
    // acting on it.
    if (goalRunRef.current !== content.goalRunId) return
    if (goalAutoOpenedRef.current.has(activeTabId)) return
    if (contentPanelTab) return // something is already open — don't hijack it
    goalAutoOpenedRef.current.add(activeTabId)
    openContentPanel("goal")
  }, [goalAnalysisOn, isBriefTab, activeTabId, content.goalRunId,
      contentPanelTab, openContentPanel])

  // ── Goal Analysis, conducted IN THE THREAD ────────────────────────────────
  //
  // Both gates are a CONVERSATION, so they happen in the conversation. The run
  // asks what the goal means; the user answers. It states what it will read;
  // the user approves, or drops a source, or says what they already expect.
  // Only then does it read anything, and only the finished report — which is a
  // document, not a question — goes to the panel.
  //
  // WHY NOT THE PANEL FOR THE GATES. It was, and the thread went quiet the
  // moment a goal was typed: the question that decides what the whole run means
  // was answered somewhere other than the conversation it belonged to, and
  // scrolling back later showed a goal with no record of what was agreed. A PM
  // has to defend the result, and the defence is exactly this exchange.
  //
  // NOTHING ADVANCES ON ITS OWN. Each gate waits for a click. The poll below
  // only watches for the run REACHING a gate; it never answers one.
  const goalGateBusyTurnRef = useRef<string | null>(null)
  const [goalGateBusyTurnId, setGoalGateBusyTurnId] = useState<string | null>(null)

  // SCOPED TO ONE TAB. `goal-plan-${runId}` is a deterministic, persisted turn
  // id, so the same conversation open in two tabs holds the same id twice —
  // patching every tab's thread would answer both. It also rebuilt every tab
  // object on every patch for no reason.
  const patchTurn = setTabsGoalGate

  /** Poll one run until it reaches one of `until`, or dies. Returns null when
   *  it failed, was cancelled, the component unmounted, or the ceiling was hit.
   *
   *  WAITS FOR A DESTINATION, NOT FOR A DEPARTURE. The first version polled
   *  "until the status leaves `queued`" — and Goal Analysis has no `queued`
   *  status at all (`crucible_runs.STATES`; a run is born `resolving_goal`).
   *  The very first tick therefore satisfied "left queued", matched no branch,
   *  and the gate was never attached: the whole feature inert, with every test
   *  still green. Naming the destination makes that class of mistake fail
   *  loudly instead of silently.
   */
  const awaitGoalRun = useCallback(
    async (
      runId: number,
      until: readonly string[],
    ): Promise<GoalRunDetail | null> => {
      const deadline = Date.now() + 10 * 60 * 1000
      while (Date.now() < deadline) {
        // Stop the moment the screen is gone: this loop outlives a closed tab
        // otherwise, and its resolve path writes a turn into a conversation
        // nobody is looking at.
        if (!mountedRef.current) return null
        try {
          const detail = await goalAnalysisApi.get(runId)
          if (until.includes(detail.status)) return detail
          if (detail.status === "failed" || detail.status === "cancelled") {
            return detail
          }
        } catch (e) {
          // A 403/404 is a verdict — the run is gone or not ours — and polling
          // it for ten minutes helps nobody. Only a transient keeps the loop.
          const st = e instanceof ApiError ? e.status : 0
          if (st === 403 || st === 404) return null
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
      return null
    },
    [],
  )

  /** A REJECTED request is not a LOST one, and they need opposite handling.
   *
   *  422/413 mean the server refused the body BEFORE claiming anything: the run
   *  is still sitting at its gate, nothing is running, and the reader has to be
   *  able to fix what they wrote and try again. Say what the server said.
   *
   *  Anything else may have been lost AFTER the claim — the server claims the
   *  row before it does any work — so the run may well be going with nothing
   *  watching it. Telling the reader to click again would 409 forever against
   *  their own successful claim; the caller polls instead and lets the run say
   *  what happened. Carried over from the panel, which owned these gates
   *  before they moved into the thread.
   */
  const goalRefusalMessage = useCallback((e: unknown): string | null => {
    // STRUCTURAL, not `instanceof`. The error crosses a module boundary (and,
    // under a mocked `lib/api`, a second copy of the class), so an identity
    // check silently answers "not a refusal" and sends a plain 422 down the
    // lost-response path — the reader is told we are checking, forever, about
    // a request the server already rejected outright.
    const raw = (e as { status?: unknown })?.status
    const status = typeof raw === "number" ? raw : 0
    if (status !== 422 && status !== 413) return null
    const body = (e as { body?: unknown })?.body
    const msg = body == null || typeof apiErrorMessage !== "function"
      ? "" : apiErrorMessage(status, body)
    return (msg && msg !== `Request failed (${status})` ? msg.trim() : "")
      || "That was not accepted. Shorten what you wrote and try again."
  }, [])

  /** The run itself is over — there is nothing to retry, so the gate goes and
   *  the reason stays in the thread. Distinct from `failGoalTurn`, which is for
   *  a refused ACTION against a run still sitting at its gate. */
  const endGoalTurn = useCallback(
    (tabId: string, turnId: string, reason: string) => {
      goalGateBusyTurnRef.current = null
      setGoalGateBusyTurnId(null)
      // The settled record SURVIVES. A definition the reader confirmed is the
      // thing they would have to defend later; overwriting it with the failure
      // threw away the answer and kept only the complaint. The failure rides
      // `goalGateError` beside it instead.
      setTabs((prev) => prev.map((t) => (t.id === tabId
        ? {
            ...t,
            thread: t.thread.map((tn) => (tn.id === turnId
              ? {
                  ...tn,
                  goalGate: undefined,
                  // EITHER the record OR the note, never both with the same
                  // text — writing both printed the sentence twice on three of
                  // the four paths that end a run.
                  //
                  // And the note is CLEARED when the failure becomes the
                  // record, because `failGoalTurn` may have left a "Checking…"
                  // there moments earlier: without this, a run that turned out
                  // to be dead rendered its verdict directly above the promise
                  // to keep checking that the verdict just answered.
                  ...(tn.goalGateResolved
                    ? { goalGateError: reason }
                    : {
                        goalGateError: undefined,
                        goalGateResolved: { kind: "failed" as const, reason },
                      }),
                }
              : tn)),
          }
        : t)))
    },
    [setTabs],
  )

  const failGoalTurn = useCallback(
    (tabId: string, turnId: string, message: string) => {
      goalGateBusyTurnRef.current = null
      setGoalGateBusyTurnId(null)
      // KEEPS THE GATE. A refused confirm or approve usually leaves the run
      // exactly where it was server-side, so the card has to stay answerable —
      // clearing it locked the reader out of a run that was still waiting for
      // them. `error` is wrong for the same reason and worse: it renders the
      // generic "There was an interruption, try again." and throws the reason
      // away entirely.
      patchTurn(tabId, turnId, { goalGateError: message })
    },
    [patchTurn],
  )

  // Gate 1 → Gate 2. The definition the user confirmed is THEIR words, which may
  // be an edit of what was proposed; it is sent verbatim.
  //
  // `runId` COMES OFF THE GATE, not a side map. A `Map` on a ref does not
  // survive a reload, but the thread does (sessionStorage) — so the card came
  // back enabled after F5 with no way to resolve its run, and Confirm was a
  // live-looking button that issued no request and showed no error. The gate
  // object already carries the id it needs.
  const confirmGoalDefinition = useCallback(
    async (tabId: string, turnId: string, runId: number, definition: string) => {
      if (goalGateBusyTurnRef.current) return
      goalGateBusyTurnRef.current = turnId
      setGoalGateBusyTurnId(turnId)
      try {
        await goalAnalysisApi.confirm(runId, definition)
        // The record of what was agreed stays in the thread.
        // CLEARS THE GATE, not just settles it. `GoalGateCard` renders the
        // settled card before it looks at `gate`, so a leftover `goalGate` is
        // invisible — but the restore's guard reads exactly that field, so an
        // answered definition went on claiming "this run is already on screen"
        // and blocked the rebuild of the PLAN gate. The dead end simply moved
        // from gate 1 to gate 2, and only needed an unmount to reach.
        patchTurn(tabId, turnId, {
          goalGate: undefined,
          goalGateResolved: { kind: "definition", definition },
        })
        const detail = await awaitGoalRun(runId, ["awaiting_approval"])
        if (!detail || detail.status !== "awaiting_approval") {
          endGoalTurn(tabId, turnId,
            "The analysis could not build a plan for this goal.")
          return
        }
        if (detail.prioritisation?.plan) {
          // A NEW turn: the plan is the run's next thing to say, and a reply
          // belongs beside the question it answers rather than replacing it.
          // PINNED to the tab that asked — minutes may have passed and the
          // reader may be somewhere else entirely.
          emitCommandTurn({
            id: `goal-plan-${runId}`,
            query: "",
            goalGate: { kind: "plan", runId, plan: detail.prioritisation.plan },
          }, tabId)
        }
      } catch (e) {
        const refused = goalRefusalMessage(e)
        if (refused) {
          // Retryable: the run never moved, so the card stays answerable.
          failGoalTurn(tabId, turnId, refused)
          return
        }
        failGoalTurn(tabId, turnId,
          "We could not tell whether that started. Checking…")
        const after = await awaitGoalRun(runId, ["awaiting_approval"])
        if (!after || after.status === "failed" || after.status === "cancelled") {
          // The twin of the guard `approveGoalPlan` got: without it a dead run
          // left a live Confirm button sitting over a permanent "Checking…".
          endGoalTurn(tabId, turnId,
            "That analysis stopped before it could build a plan.")
          return
        }
        if (after.status === "awaiting_approval" && after.prioritisation?.plan) {
          patchTurn(tabId, turnId, {
            goalGate: undefined,
            goalGateError: undefined,
            goalGateResolved: { kind: "definition", definition },
          })
          emitCommandTurn({
            id: `goal-plan-${runId}`,
            query: "",
            goalGate: { kind: "plan", runId, plan: after.prioritisation.plan },
          }, tabId)
        }
      } finally {
        goalGateBusyTurnRef.current = null
        setGoalGateBusyTurnId(null)
      }
    },
    [awaitGoalRun, emitCommandTurn, endGoalTurn, failGoalTurn,
     goalRefusalMessage, patchTurn],
  )

  // Gate 2 → the run. Only here does anything get read, and only here does the
  // panel earn its place: what follows is a document.
  const approveGoalPlan = useCallback(
    async (tabId: string, turnId: string, runId: number, decision: PlanDecision,
           plan?: SettledPlan) => {
      if (goalGateBusyTurnRef.current) return
      goalGateBusyTurnRef.current = turnId
      setGoalGateBusyTurnId(turnId)
      try {
        await goalAnalysisApi.approve(runId, {
          excluded_sources: decision.excluded_sources,
          hypotheses: decision.hypotheses,
        })
        patchTurn(tabId, turnId, {
          goalGate: undefined,
          goalGateResolved: {
            kind: "plan",
            excludedSources: decision.excluded_sources,
            hypotheses: decision.hypotheses,
            plan,
          },
        })
        goalRunRef.current = runId
        setContent({ goalRunId: runId })
        goalAutoOpenedRef.current.add(tabId)
        openContentPanel("goal")
      } catch (e) {
        const refused = goalRefusalMessage(e)
        if (refused) {
          // The server refused the body before claiming anything: the run is
          // still at its gate and the card stays answerable.
          failGoalTurn(tabId, turnId, refused)
          return
        }
        // AND THEN ACTUALLY CHECK. The server claims the row before it does any
        // work, so a response lost after that claim means the run IS going with
        // nothing watching it. Saying "Checking…" and then not checking is
        // worse than saying nothing: it promises a resolution that never comes.
        failGoalTurn(tabId, turnId,
          "We could not tell whether that started. Checking…")
        const after = await awaitGoalRun(runId, ["running", "ready"])
        // `awaitGoalRun` also returns on `failed`/`cancelled` — that is a
        // verdict, not a destination. Treating any non-null answer as "it
        // started" reported a dead run as a running one and opened the panel
        // onto it.
        if (!after || (after.status !== "running" && after.status !== "ready")) {
          endGoalTurn(tabId, turnId,
            "That analysis stopped before it could read anything.")
          return
        }
        patchTurn(tabId, turnId, {
          goalGateError: undefined,
          goalGateResolved: {
            kind: "plan",
            excludedSources: decision.excluded_sources,
            hypotheses: decision.hypotheses,
            plan,
          },
        })
        goalRunRef.current = runId
        setContent({ goalRunId: runId })
        goalAutoOpenedRef.current.add(tabId)
        openContentPanel("goal")
      } finally {
        goalGateBusyTurnRef.current = null
        setGoalGateBusyTurnId(null)
      }
    },
    [awaitGoalRun, endGoalTurn, failGoalTurn, goalRefusalMessage, patchTurn,
     setContent, openContentPanel],
  )

  // The entry point: start the run, then put its FIRST question in the thread.
  // The panel is not opened here — there is nothing document-shaped to show yet.
  const startGoalAnalysis = useCallback(async (
    goalText: string,
    /** WHAT THE USER ACTUALLY TYPED. The planner hands back an EXTRACTED goal
     *  ("increase revenue by 5%") which is what the run should work from — but
     *  it is not what the reader said. Emitting the extraction as their message
     *  rewrote "How can I increase revenue by 5%?" into "increase revenue by
     *  5%" in their own thread, so scrolling back showed them saying something
     *  they never said. The run gets the extraction; the transcript gets the
     *  sentence. Falls back to the extraction for callers that have no raw text
     *  (the `+` menu, where the two are the same thing). */
    saidText?: string,
  ) => {
    const turnId = `goal-${Date.now()}`
    // THE TURN GOES UP FIRST, before the network call. The dispatcher reports
    // `handled` from this executor's PRESENCE, so by the time we run, the caller
    // has already rolled the optimistic turn away — if the start then 403s or
    // times out and we had emitted nothing, the user's message would simply
    // vanish from the thread. That is not hypothetical: it is what the deployed
    // build does today, observed on staging (goal typed, panel opens, thread
    // shows an empty "New chat"). Emitting first means the worst case is a turn
    // carrying an error, which is still a conversation.
    emitCommandTurn({
      id: turnId,
      query: (saidText || "").trim() || goalText,
      // A GATE FROM THE FIRST FRAME. The run spends a beat in `resolving_goal`
      // before it has a question, and a turn with no gate and no reply runs the
      // ordinary no-reply ladder — which printed "No response was generated for
      // this message." over a run that was working perfectly.
      goalGate: { kind: "pending", goalText },
    })
    // AFTER the emit: on a fresh or brief surface `emitCommandTurn` spawns the
    // tab, so reading this first returned the tab the reader was leaving (or
    // null, silently abandoning a turn already on screen).
    const tabId = activeTabIdRef.current
    if (!tabId) return
    try {
      // WAIT FOR THE CONVERSATION ROW, briefly. On the first message of a brand
      // new chat there is no `dbConvId` yet — `emitCommandTurn` has only just
      // queued its creation — so the run was started with no `conversation_id`
      // and stayed orphaned from its own chat forever: the restore matches runs
      // by conversation, so that run could never come back to the thread it was
      // started in. Reading it from the tab after the emit costs a moment on a
      // path that is already a second from its first question, and only on a
      // first message.
      let convId = activeConvId
      // TEN SECONDS, NOT TWO. Two was chosen as "a moment" and is really a bet
      // on how fast the conversation insert lands; a cold backend or a slow
      // link loses that bet and the reader is refused for a save that was
      // seconds from arriving. Waiting longer costs nothing on the ordinary
      // path — the loop exits the instant the row appears — and the turn is
      // already on screen in its pending state while it runs.
      for (let i = 0; convId == null && i < 100; i++) {
        await new Promise((r) => setTimeout(r, 100))
        if (!mountedRef.current) return
        convId = tabsRef.current.find((t) => t.id === tabId)?.dbConvId ?? null
      }
      if (convId == null) {
        // THE WAIT CAN FAIL, and failing quietly is how the original bug
        // looked. If the conversation row never arrives — create failed, or we
        // are offline — starting anyway produces a run bound to no chat, which
        // the restore (which matches BY conversation) can never bring back.
        // The reader has just waited two seconds for that outcome, so they are
        // told it rather than left with a run that silently cannot return.
        // AND IT SAYS THE TRUE THING. "Send a message first" was advice the
        // reader had already taken — the goal they just sent IS the message —
        // and "has not been saved yet" reads as a permanent state rather than
        // a save that did not finish in time. What they can actually do is
        // ask again.
        endGoalTurn(tabId, turnId,
          "This chat had not finished saving, so the analysis could not be "
          + "attached to it — starting it anyway would have left it unable to "
          + "find its way back here. Ask again in a moment.")
        return
      }
      const run = await goalAnalysisApi.start(goalText, {
        ...(convId != null ? { conversation_id: convId } : {}),
      })
      goalRunRef.current = run.id
      // A run is born `resolving_goal` and reaches the gate a moment later.
      const detail = await awaitGoalRun(run.id, ["awaiting_confirmation"])
      if (!detail || detail.status !== "awaiting_confirmation") {
        endGoalTurn(tabId, turnId,
          "The analysis could not start for this goal.")
        return
      }
      patchTurn(tabId, turnId, {
        goalGate: {
          kind: "definition",
          runId: run.id,
          goalText,
          ask: detail.prioritisation?.ask
            || "Before this runs, confirm what this goal means.",
          proposedDefinition: detail.prioritisation?.proposed_definition,
          proposedSource: detail.prioritisation?.proposed_source,
          methodNote: detail.prioritisation?.method_note,
        },
      })
    } catch (e) {
      // A 403 here is the entitlement gate, and it is the one failure worth
      // naming precisely: the control was visible, so "something went wrong"
      // would read as a bug rather than as "your company is not on this yet".
      const denied = e instanceof ApiError && e.status === 403
      showToast(
        denied ? "Goal Analysis is not enabled" : "Could not start the analysis",
        denied
          ? "This is an experimental feature and your company is not enrolled yet."
          : (e instanceof Error ? e.message : String(e)).slice(0, 200),
      )
      // The toast is transient and the thread is not. There is no run to go
      // back to here, so this ends the turn rather than offering a retry.
      endGoalTurn(tabId, turnId, denied
        ? "Goal Analysis is not enabled for this workspace yet."
        : "That analysis could not be started.")
    }
  }, [activeConvId, awaitGoalRun, emitCommandTurn, endGoalTurn, patchTurn,
      showToast])

  // Republished on every render so the dispatcher always calls the current
  // closure. An effect would work equally well here — `dispatchChatIntent`
  // runs in `submitAsk`'s continuation after an await, so it is always past
  // commit — and the only reason this is a bare assignment is that there is
  // nothing to clean up and no dependency list to keep honest. The write is
  // idempotent, so a discarded or doubled render (StrictMode, concurrent)
  // stores the same value twice and nothing observes the difference.
  startGoalAnalysisRef.current = startGoalAnalysis

  // Goal mode intercepts the composer submit BEFORE the ask path: a run takes
  // the goal in the user's own words, so a slash trigger spliced into the front
  // of it (which the engine's submit would do) must never happen. Wrapped at the
  // host so the shared engine's `handleComposerSubmit` stays surface-agnostic;
  // a normal send falls straight through to it.
  const handleGoalOrComposerSubmit = useCallback(() => {
    if (!goalMode) {
      handleComposerSubmit()
      return
    }
    const q = draft.trim()
    // Match the engine's own guards so goal mode behaves identically under a
    // too-short draft, an in-flight ask, or a send already mid-dispatch.
    if (q.length < DRAFT_MIN_CHARS) {
      if (q.length > 0) showToast("Question too short", "Use at least 3 characters.")
      return
    }
    if (busy) {
      showComposerHint("busy")
      return
    }
    if (pendingSend) return
    setDraft("")
    setGoalMode(false)
    setPlusMenuOpen(false)
    if (voice.listening) voice.cancel()
    void startGoalAnalysis(q)
    const ta = composerRef.current
    if (ta) ta.style.height = ""
  }, [
    goalMode, handleComposerSubmit, draft, busy, pendingSend, voice,
    setDraft, setPlusMenuOpen, startGoalAnalysis, composerRef, showComposerHint,
  ])

  // Enter-to-send must respect goal mode too. Only the plain Enter is overridden
  // (no palette open, no modifier); ⌘/, the slash-palette navigation, and Esc
  // all stay with the engine's keydown.
  const handleGoalOrComposerKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (goalMode && e.key === "Enter" && !e.shiftKey && !slashOpen && !(e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        handleGoalOrComposerSubmit()
        return
      }
      handleComposerKeyDown(e)
    },
    [goalMode, slashOpen, handleGoalOrComposerSubmit, handleComposerKeyDown],
  )

  // The `+` menu's third item (present only while enrolled — ChatComposer
  // appends it last so indices 0/1 keep meaning what they always meant) turns on
  // goal mode; everything else falls through to the composer's own handler.
  const handleGoalOrPlusMenuSelect = useCallback((index: number) => {
    if (index === 2) {
      setPlusMenuOpen(false)
      setGoalMode(true)
      composerRef.current?.focus()
      return
    }
    handlePlusMenuSelect(index)
  }, [handlePlusMenuSelect, setPlusMenuOpen, composerRef])

  /** The composer's one status line. A dictation problem outranks the busy hint:
   *  the busy hint answers a key you just pressed and expires on its own, while
   *  a blocked microphone is a state you stay stuck in until you go and change a
   *  browser setting. */
  const composerHintNode: React.ReactNode = voice.error
    ? voice.error
    : composerHint === "busy"
      ? <>{BUSY_ENTER_HINT_LEAD}<b>Stop</b>{BUSY_ENTER_HINT_TAIL}</>
      : null

  // main's inline `renderComposer` is dropped: in this branch the composer is
  // rendered by the shared `ConversationView` (landing + dock), which is handed
  // the goal-mode props below. Keeping a second ChatComposer here would let the
  // two drift.
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
            // The main turn-mapping dependency bag — annotated so the
            // share_to_slack wrapper params below get their contextual types
            // (else they'd be implicit-any). Handed straight to ConversationView,
            // which calls mapMainTurns(thread, mapDeps) and reads a handful of
            // these fields for its own render.
            const mapDeps: MapMainTurnsDeps = {
              animatedTurnIds, askStartRef, resumedTurnsRef, lastLiveTurnIdx,
              busy, activeTab, name, userInitials, skillForQuery,
              ticketSetActionState, showInsightMsg, chatEvidenceExists,
              chatPrdExists, chatPrdCtaWaiting, chatProtoPrdId, chatPrototypeReady,
              inlinePrdCards, inlinePrdAnchorIdx, insightCardNode, prdQuestionsNode,
              clarifyPopupOpen, pendingClarifyTurn,
              // WITHOUT THESE THE CARD IS DECORATION. They are optional on
              // `MapMainTurnsDeps` (the group surface has no Goal Analysis), so
              // omitting them here type-checked cleanly and every button
              // short-circuited through `confirmGoalDefinition?.(…)` — the
              // second independent way this feature shipped inert.
              goalGateBusyTurnId,
              confirmGoalDefinition,
              approveGoalPlan,
              handleAskAgain, handleStopAsk, submitClarifyAnswers, setViewerAttachment,
              editingTurnId,
              copiedTurnId,
              onCopyTurn: handleCopyTurn,
              onRetryTurn: handleRetryTurn,
              onEditTurn: handleEditTurn,
              onSubmitTurnEdit: handleSubmitTurnEdit,
              onCancelTurnEdit: handleCancelTurnEdit,
              openReportByTitle, openArtifactInPanel, openChatArtifactItem,
              handleTicketSetAction, handleOpenEvidence, handleOpenPrd,
              handleViewPrototype, handlePrototypeSettled,
              // share_to_slack — the preview card riding a turn. The SEND is
              // the only one of these that reaches Slack, and only after the
              // user presses the button in the card.
              onSendSlackShare: (turnId, channelId, note) =>
                void sendSlackShare(turnId, channelId, note),
              onCancelSlackShare: (turnId) =>
                patchSlackShare(activeTab!.id, turnId, {
                  resolved: { outcome: "cancelled" },
                }),
              onPickSlackShareTarget: (turnId, target) =>
                void repreviewSlackShare(turnId, target),
            }
            // The main-chat active-conversation render, lifted verbatim into the
            // shared ConversationView (still driven by this screen's inline
            // engine via props). A surface:"main" descriptor is a structural
            // no-op — no project seam is reachable.
            return (
              <ConversationView
                thread={thread}
                mapDeps={mapDeps}
                draft={draft}
                pinnedSkill={pinnedSkill}
                attachments={attachments}
                composerHintNode={composerHintNode}
                plusMenuOpen={plusMenuOpen}
                plusMenuActive={plusMenuActive}
                slashOpen={slashOpen}
                filteredSkills={filteredSkills}
                slashActive={slashActive}
                composerRef={composerRef}
                fileInputRef={fileInputRef}
                voice={voice}
                handleSlashSelect={handleSlashSelect}
                setSlashActive={setSlashActive}
                handleComposerInput={handleComposerInput}
                handleComposerKeyDown={handleGoalOrComposerKeyDown}
                handleComposerSubmit={handleGoalOrComposerSubmit}
                setPlusMenuActive={setPlusMenuActive}
                setPlusMenuOpen={setPlusMenuOpen}
                handlePlusMenuSelect={handleGoalOrPlusMenuSelect}
                goalMode={goalMode}
                onExitGoalMode={() => setGoalMode(false)}
                goalModeAvailable={goalAnalysisOn}
                setAttachments={setAttachments}
                setPinnedSkill={setPinnedSkill}
                handleFileSelect={handleFileSelect}
                handleToggleVoice={handleToggleVoice}
                showChipRow={showChipRow}
                displayChips={displayChips}
                handleHomeCard={handleHomeCard}
                handleStarterChip={handleStarterChip}
                showEmptyStarters={showEmptyStarters}
                activeTab={activeTab}
                pendingSendHere={pendingSendHere}
                pendingSend={pendingSend}
                pendingClarifyTurn={pendingClarifyTurn}
                setClarifyPopupDismissed={setClarifyPopupDismissed}
                assignPopupOpen={assignPopupOpen}
                pendingAssignState={pendingAssignState}
                activeTabId={activeTabId}
                completeAssign={completeAssign}
                cancelAssign={cancelAssign}
                sharePopupOpen={sharePopupOpen}
                pendingShareState={pendingShareState}
                completeShareQuestion={completeShareQuestion}
                cancelShareQuestion={cancelShareQuestion}
                setQuestionDockEl={setQuestionDockEl}
                nextPrompts={nextPrompts}
                submitAsk={submitAsk}
                showThreadView={showThreadView}
                threadScrollRef={threadScrollRef}
                handleThreadScroll={handleThreadScroll}
                setThreadContentEl={setThreadContentEl}
                quote={quote}
                onRemoveQuote={() => setQuote(null)}
                onQuoteSelection={handleQuoteSelection}
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
