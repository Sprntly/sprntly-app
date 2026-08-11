"use client"

// ── ProjectIndividualChat — "My chat with Sprntly" (private, per project) ──
//
// AD-P13 (one chat presentation layer): a THIN container, same reuse
// discipline as `ProjectGroupChat`. It composes the shared primitives
// (`AskReplyBody`, `ReactMarkdown`+`remarkGfm`, `AssistantThinkingSkeleton`/
// `AssistantWaitState`, `OpenArtifactChips`, `app-icons`) + the extracted
// `shared/ChatComposer` — no bespoke bubble/markdown/composer of its own.
//
// It does NOT mount the app's existing multi-tab chat container: that
// component takes no props and is wired to an unrelated (insight-tabs) data
// model, so reusing it here would mean either forking it or deep-modifying
// it — both against AD-P13/AD-P2. Instead this hits `/v1/ask` directly with
// `project_id` via the SHARED ask library (`runAskGeneration`/`askApi`,
// `web/app/lib/` — plain functions, not internal to any chat container), the
// same POST → poll `GET /v1/ask/{id}` → render cycle every other Ask surface
// in this app already uses. The project's memory (summary + top-N entries)
// and the caller's job_role are folded in SERVER-SIDE by the backend when
// `project_id` is set (`backend/app/routes/ask.py`, build spec AD-P8) — this
// component sends the id and renders the answer, nothing more.
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AskReplyBody } from "../../../shared/AskReplyBody"
import { AssistantThinkingSkeleton } from "../../../shared/AssistantThinkingSkeleton"
import { AssistantWaitState } from "../../../shared/AssistantWaitState"
import { OpenArtifactChips } from "../../../shared/OpenArtifactChips"
import { ChatComposer, DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
import { AGENT_NAME } from "../../../../lib/agent"
import { useCompany } from "../../../../context/CompanyContext"
import {
  runAskGeneration,
  resumeAskGeneration,
  getPendingAsk,
  AskStoppedError,
  AskCancelledError,
  AskTimeoutError,
} from "../../../../lib/runAskGeneration"
import type { AskResponse, OpenArtifactCandidate } from "../../../../lib/api"
import styles from "./ProjectIndividualChat.module.css"

const COMPOSER_PLACEHOLDER = "Message Sprntly…"

/** One question+answer pair in this private thread. Purely client-side and
 *  in-memory — the individual chat has no group-turn table to poll; each
 *  send is one `/v1/ask` job (build spec §5.3/§5.4 draw the same line: a
 *  human-to-human write is cheap, an agent turn is the metered one). */
type LocalTurn = {
  id: string
  question: string
  reply: AskResponse | null
  pending: boolean
  stopped: boolean
  error: string | null
}

export type ProjectIndividualChatProps = {
  projectId: number | string
  /** Opens the artifacts modal on a specific candidate — no candidate source
   *  exists on a plain `AskResponse` yet (the `open_artifact` envelope is a
   *  separate intent-classification path this component deliberately does
   *  not reimplement), so `OpenArtifactChips` always renders zero chips
   *  today. Composed anyway (AD-P13 — the primitive, not a bespoke chip) so
   *  wiring real candidates later is additive. */
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void
  /** The cross-chat INSIGHT turn (design-spec AC7/AC11) — a note surfaced
   *  from the group chat, rendered with the SAME `bc-turn--insight`/
   *  `bc-insight-msg-kind` CSS the app's existing insight-opening card wears
   *  (read-only class reuse, not a second implementation). No data source
   *  feeds this yet; omitted (the default) renders nothing. */
  insightNote?: { by: string; text: string } | null
}

function formatTime(d: number): string {
  return new Date(d).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
}

export function ProjectIndividualChat({ projectId, onOpenArtifact, insightNote }: ProjectIndividualChatProps) {
  const { activeCompany } = useCompany()
  const [turns, setTurns] = useState<LocalTurn[]>([])
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)

  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const stoppedRef = useRef(false)
  // Stable per-project scope for the shared ask-job persistence
  // (jobResume) — the same "one Ask in flight" contract every other Ask
  // surface in this app already keeps.
  const tabId = useMemo(() => `project-individual-${projectId}`, [projectId])
  const [resuming, setResuming] = useState(false)

  // A reload/remount mid-answer must not orphan the job (the same resume
  // contract every other Ask surface keeps, via the SAME shared
  // `getPendingAsk`/`resumeAskGeneration` — no new persistence mechanism).
  // This thin component keeps no history, so the resumed turn's question
  // text can't be reconstructed locally; the answer still lands rather than
  // silently disappearing.
  useEffect(() => {
    const pending = getPendingAsk(activeCompany, tabId)
    if (!pending) return
    const askId = Number(pending.id)
    if (!Number.isFinite(askId)) return
    setResuming(true)
    resumeAskGeneration(askId, activeCompany, tabId)
      .then((reply) => {
        setTurns((prev) => [
          ...prev,
          { id: `resumed-${askId}`, question: "(your previous message)", reply, pending: false, stopped: false, error: null },
        ])
      })
      .catch(() => {
        /* stopped/cancelled/failed resumes are silently dropped — nothing
           was showing for this job before the remount either */
      })
      .finally(() => setResuming(false))
    // Runs once per (company, tabId) — a fresh mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSend = useCallback(() => {
    const question = draft.trim()
    if (question.length < DRAFT_MIN_CHARS || busy) return
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    setTurns((prev) => [...prev, { id, question, reply: null, pending: true, stopped: false, error: null }])
    setDraft("")
    setBusy(true)
    stoppedRef.current = false

    runAskGeneration(question, activeCompany, tabId, {
      project_id: Number(projectId),
      isStopped: () => stoppedRef.current,
    })
      .then((reply) => {
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, reply, pending: false } : t)))
      })
      .catch((err: unknown) => {
        if (err instanceof AskStoppedError) {
          setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, stopped: true } : t)))
          return
        }
        if (err instanceof AskCancelledError) {
          // The surface went away mid-poll — nothing to render into.
          return
        }
        const message =
          err instanceof AskTimeoutError
            ? "This is taking longer than expected. It's still running on our side."
            : "That answer didn't come through. Try again."
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, pending: false, error: message } : t)))
      })
      .finally(() => setBusy(false))
  }, [draft, busy, activeCompany, tabId, projectId])

  const handleStop = useCallback(() => {
    stoppedRef.current = true
  }, [])

  return (
    <div className={styles.thread} data-testid="project-individual-chat">
      <div className={styles.scroll} data-testid="individual-chat-scroll">
        {insightNote ? (
          <div className="bc-turn bc-turn--insight" data-testid="cross-chat-insight">
            <span className="bc-insight-msg-kind">INSIGHT</span>
            <span>
              <b>{insightNote.by}</b> noted this in the group chat: {insightNote.text}
            </span>
          </div>
        ) : null}

        {resuming ? (
          <div data-testid="ic-resuming">
            <AssistantThinkingSkeleton phase="Picking up where you left off…" />
          </div>
        ) : null}

        {!resuming && turns.length === 0 ? (
          <div className={styles.empty} data-testid="individual-chat-empty">
            Ask Sprntly anything about this project — it already knows what the team has covered.
          </div>
        ) : null}

        {turns.map((turn) => (
          <div key={turn.id} className={styles.pair}>
            <div className={styles.userTurn} data-testid="ic-msg-you">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.question}</ReactMarkdown>
            </div>
            {turn.pending ? (
              <div className={styles.agentTurn} data-testid="ic-msg-pending">
                <AssistantWaitState compact />
              </div>
            ) : turn.stopped ? (
              <div className={styles.agentTurn} data-testid="ic-msg-stopped">
                You stopped this response.
              </div>
            ) : turn.error ? (
              <div className={styles.agentTurn} role="alert" data-testid="ic-msg-error">
                {turn.error}
              </div>
            ) : turn.reply ? (
              <div className={styles.agentTurn} data-testid="ic-msg-agent">
                <div className={styles.agentHead}>
                  <span className={styles.agentName}>{AGENT_NAME}</span>
                  <span className={styles.time}>{formatTime(Date.now())}</span>
                </div>
                <AskReplyBody reply={turn.reply} />
                <OpenArtifactChips candidates={[]} onOpen={(c) => onOpenArtifact?.(c)} />
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <div className={styles.composerWrap}>
        <ChatComposer
          busy={busy}
          draft={draft}
          pinnedSkill={null}
          attachments={[]}
          hint={null}
          menuOpen={false}
          menuActiveIndex={0}
          slashMenu={null}
          composerRef={composerRef}
          fileInputRef={fileInputRef}
          onInput={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          onSend={handleSend}
          onStop={handleStop}
          onToggleMenu={() => {}}
          onMenuActive={() => {}}
          onMenuSelect={() => {}}
          onCloseMenu={() => {}}
          onRemoveAttachment={() => {}}
          onRemoveSkill={() => {}}
          onFileSelect={() => {}}
          voiceSupported={false}
          voiceListening={false}
          onToggleVoice={() => {}}
          placeholder={COMPOSER_PLACEHOLDER}
        />
      </div>
    </div>
  )
}
