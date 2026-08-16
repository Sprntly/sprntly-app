"use client"

/**
 * The one scope-parameterized chat shell. It owns the frame `<main>`, the
 * scroll viewport, the thread column, the transcript mount, the dock, and the
 * attachment-overlay mount — the layout every chat surface shares — and renders
 * it through a typed `ChatSurfaceDescriptor`.
 *
 * `surface: "main"` is a structural NO-OP alias: every project seam is
 * absent/default and no project code path is reachable. The shell never reads
 * `projectId`, roster, `multiParty`, the draft API, or mention wiring on the
 * main path, and it statically imports none of the project-only leaf modules
 * — those arrive only as descriptor-injected nodes/closures constructed by the
 * project hosts. This mirrors the backend's `SurfaceScope.is_noop` discipline
 * one layer up.
 *
 * On the main path the host keeps its render pass whole: the finished
 * `ChatTranscriptTurn[]` (produced by the host-called `mapMainTurns`), the
 * pending-send bubble, and the composer all arrive host-rendered; the shell
 * only lays them out. Scroll behaviour (pin tracking, jump effects, the
 * ResizeObserver) stays host-side, operating through the `refs` channel — the
 * shell renders the nodes, it never owns main's scroll logic.
 *
 * `surface !== "main"` (project surfaces) is the single-party rendering path:
 * the shell receives the engine's normalized `ShellTurn[]`, maps each turn
 * through the descriptor's `renderUserBody`/`renderAgentBody`/`turnFooter`
 * closures + `pickOptions`, constructs the `ChatComposer` internally from
 * `descriptor.composer`, owns the standalone project viewport with an internal
 * pinned-follow scroll, and owns the `escToStop` listener. The project path is
 * reachable ONLY when `surface !== "main"`; the main return below is
 * byte-unchanged.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react"
import { ChatTranscript, type ChatTranscriptTurn } from "../ChatTranscript"
import { ChatComposer, DRAFT_MIN_CHARS } from "../ChatComposer"
import type { ChatShellHandle, ChatSurfaceDescriptor, ShellTurn } from "./types"
import shellStyles from "./ChatShell.module.css"

export interface ChatShellProps {
  descriptor: ChatSurfaceDescriptor
  /** The finished turns. Main: the host-mapped `ChatTranscriptTurn[]`
   *  (`mapMainTurns` output), consumed byte-identically. Project surfaces: the
   *  engine's normalized `ShellTurn[]`, mapped by the shell's project path. */
  turns: ChatTranscriptTurn[] | ShellTurn[]
  /** The optimistic pending-send bubble, host-rendered (main only; project
   *  surfaces carry their optimistic turn inside `turns`). */
  pendingSend?: ReactNode
  /** The composer, host-rendered for the main path (its dual-mode closure).
   *  Project surfaces ignore it — the shell constructs `ChatComposer` from
   *  `descriptor.composer` instead. */
  composerNode?: ReactNode
  /** The attachment overlay, host-rendered; gated by
   *  `descriptor.overlays.attachmentViewer`. */
  attachmentViewer?: ReactNode
  /** Project surfaces only: invoked when a `pickOptions` choice is selected
   *  (private's clarify-PRD-pick). The host wires this to the engine's pick
   *  closure; main never renders `pickOptions`, so this is never called. */
  onPickOption?: (turnId: string, option: { id: string; title: string; instruction?: string }) => void
}

/** Data-driven turn timestamp (project surfaces, `timestamps: "fromTurn"`). */
function formatShellTime(d: number): string {
  return new Date(d).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
}

function ChatShellInner(
  { descriptor, turns, pendingSend, composerNode, attachmentViewer, onPickOption }: ChatShellProps,
  ref: React.Ref<ChatShellHandle>,
) {
  const { frame, transcript, dock, overlays, refs, composer, send } = descriptor
  const isThread = frame.mode === "thread"
  const isMain = descriptor.surface === "main"

  // Project-surface state (unused on the main path — declared unconditionally
  // for rules-of-hooks; none of it is read or rendered when `isMain`).
  const [draft, setDraft] = useState("")
  const projectComposerRef = useRef<HTMLTextAreaElement>(null)
  const projectFileInputRef = useRef<HTMLInputElement>(null)
  const projectViewportRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)

  // Scroll methods are no-op-safe stubs in this wave (project surfaces wire
  // shell-owned scrolling later); on main the host owns scrolling through the
  // `refs` channel, so nothing calls these.
  useImperativeHandle(
    ref,
    (): ChatShellHandle => ({
      scrollToTurn: () => {},
      scrollToBottom: () => {},
    }),
    [],
  )

  // Esc-to-stop — project surfaces only (main keeps its own host listener).
  useEffect(() => {
    if (isMain || !composer.escToStop || !composer.stop?.onStop) return
    const onStop = composer.stop.onStop
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onStop()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [isMain, composer.escToStop, composer.stop])

  // Pinned-follow auto-scroll — project surfaces only. On new turns, stick to
  // the bottom unless the reader scrolled up (parity with main's scroll feel).
  useEffect(() => {
    if (isMain) return
    const el = projectViewportRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [isMain, turns])

  const viewportBase = frame.viewportClassName ?? "od-center-scroll"

  // ── Project-surface path (surface !== "main") ─────────────────────────────
  if (!isMain) {
    const prefix = descriptor.testIdPrefix ?? ""
    const minChars = composer.minChars ?? DRAFT_MIN_CHARS
    const projectTurns = turns as ShellTurn[]
    const projectBusy = projectTurns.some((t) => t.pending)

    const mapped: ChatTranscriptTurn[] = projectTurns.map((turn) => {
      const hasAgent =
        turn.author.kind === "agent" ||
        turn.reply != null ||
        !!turn.pending ||
        !!turn.stopped ||
        turn.error != null ||
        turn.partial != null
      const hasUser = turn.author.kind === "self" && turn.content != null
      const agentTimestamp =
        transcript.timestamps === "fromTurn" && turn.createdAt != null
          ? formatShellTime(turn.createdAt)
          : null

      // The clarify-PRD-pick options (private) render in the agent body's
      // footer position — behaviour identical to the pre-fold surface. The
      // shell owns the buttons; the pick itself is engine-side via onPickOption.
      const pickNode = turn.pickOptions?.length ? (
        <div className={shellStyles.clarifyOptions} data-testid={`${prefix}-clarify-options`}>
          {turn.pickOptions.map((opt) => (
            <button
              key={opt.id}
              type="button"
              className={shellStyles.clarifyOption}
              data-testid={`${prefix}-clarify-option-${opt.id}`}
              onClick={() => onPickOption?.(turn.id, opt)}
            >
              {opt.title}
            </button>
          ))}
        </div>
      ) : null

      const agentBodyNode = hasAgent ? (
        <>
          {transcript.renderAgentBody?.(turn)}
          {pickNode}
        </>
      ) : undefined

      // A pure agent-authored turn (a delivered brief / history assistant row)
      // carries its testid on the turn WRAPPER, so a `turnFooter` affordance
      // (delegation actions) reads as contained within it — matching the
      // pre-fold DOM. Self-authored turns keep their `ic-*` testids on the
      // body nodes (via the render closures).
      const wrapperTestId =
        turn.author.kind === "agent" && prefix ? `${prefix}-history-agent` : undefined

      return {
        turnId: turn.id,
        dataTestId: wrapperTestId,
        agentName: transcript.agentName,
        agentBadge: transcript.agentBadge ?? null,
        agentTimestamp,
        showAgent: hasAgent,
        user: hasUser
          ? {
              bodyNode: transcript.renderUserBody?.(turn),
              hideHead: transcript.userHead === "hidden",
              name: turn.author.name ?? null,
            }
          : undefined,
        agentBodyNode,
        footer: transcript.turnFooter?.(turn) ?? undefined,
      }
    })

    const submit = () => {
      const q = draft.trim()
      if (q.length < minChars || projectBusy) return
      send.onSubmit(q)
      setDraft("")
    }

    return (
      <>
        <main className="od-center od-center--thread">
          {frame.aboveViewport ?? null}
          <div
            className={viewportBase}
            ref={projectViewportRef}
            onScroll={() => {
              const el = projectViewportRef.current
              if (el) pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
            }}
          >
            <div className="bc-scroll">
              <div className={frame.threadClassName ?? "bc-thread"}>
                <ChatTranscript
                  turns={mapped}
                  leading={transcript.leading}
                  trailing={transcript.trailing}
                />
              </div>
            </div>
          </div>
          <div className={frame.dockClassName ?? "bc-dock"}>
            {dock?.aboveComposer}
            <ChatComposer
              busy={projectBusy}
              draft={draft}
              pinnedSkill={null}
              attachments={[]}
              hint={composer.hint ?? null}
              menuOpen={false}
              menuActiveIndex={0}
              slashMenu={composer.slashMenu ?? null}
              composerRef={projectComposerRef}
              fileInputRef={projectFileInputRef}
              onInput={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
              onSend={submit}
              onStop={() => composer.stop?.onStop?.()}
              onToggleMenu={() => {}}
              onMenuActive={() => {}}
              onMenuSelect={() => {}}
              onCloseMenu={() => {}}
              onRemoveAttachment={() => {}}
              onRemoveSkill={() => {}}
              onFileSelect={() => {}}
              placeholder={composer.placeholder}
            />
          </div>
        </main>
        {overlays?.attachmentViewer ? attachmentViewer : null}
      </>
    )
  }

  // ── Main-surface path (byte-unchanged) ────────────────────────────────────
  const viewportClassName = isThread
    ? viewportBase
    : `${viewportBase} od-center-scroll--home-landing`

  return (
    <>
      <main
        className={`od-center ${isThread ? "od-center--thread" : "od-center--landing"}`}
      >
        <div
          className={viewportClassName}
          ref={refs?.viewportRef}
          onScroll={refs?.onViewportScroll}
        >
          {isThread ? (
            <div className="bc-scroll">
              <div className={frame.threadClassName ?? "bc-thread"} ref={refs?.contentColumnRef}>
                <ChatTranscript turns={turns as ChatTranscriptTurn[]} leading={transcript.leading} />
                {pendingSend}
              </div>
            </div>
          ) : (
            frame.landing
          )}
        </div>

        {isThread ? (
          <div className={frame.dockClassName ?? "bc-dock"}>
            {dock?.aboveComposer}
            {composerNode}
          </div>
        ) : null}
      </main>
      {overlays?.attachmentViewer ? attachmentViewer : null}
    </>
  )
}

export const ChatShell = forwardRef<ChatShellHandle, ChatShellProps>(ChatShellInner)
ChatShell.displayName = "ChatShell"
