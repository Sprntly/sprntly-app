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
import type { ChatShellHandle, ChatSurfaceDescriptor, ComposerDraftApi, ShellTurn } from "./types"
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
  /** Project surfaces only, opt-in: called ONCE on mount with the shell's
   *  `ComposerDraftApi` (a method facade over the shell-owned draft/caret). The
   *  group host wires it so its mention picker can read/insert the live draft;
   *  private does NOT pass it, so private's flow is unchanged. Mirrors the
   *  existing `onPickOption` callback pattern; main never constructs the API. */
  onDraftApiReady?: (api: ComposerDraftApi) => void
}

/** Data-driven turn timestamp (project surfaces, `timestamps: "fromTurn"`). */
function formatShellTime(d: number): string {
  return new Date(d).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
}

function ChatShellInner(
  { descriptor, turns, pendingSend, composerNode, attachmentViewer, onPickOption, onDraftApiReady }: ChatShellProps,
  ref: React.Ref<ChatShellHandle>,
) {
  const { frame, transcript, dock, overlays, refs, composer, send, reply } = descriptor
  const isThread = frame.mode === "thread"
  const isMain = descriptor.surface === "main"

  // Project-surface state (unused on the main path — declared unconditionally
  // for rules-of-hooks; none of it is read or rendered when `isMain`).
  const [draft, setDraft] = useState("")
  const projectComposerRef = useRef<HTMLTextAreaElement>(null)
  const projectFileInputRef = useRef<HTMLInputElement>(null)
  const projectViewportRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)
  // Live mirror of the draft so `getValue()` reflects the current value even
  // between a `setDraft` and its committed render.
  const draftRef = useRef("")
  draftRef.current = draft
  // A caret offset to re-seat into the textarea after a programmatic write
  // (mention chip insertion) changed the draft — applied in the effect below.
  const pendingCaretRef = useRef<number | null>(null)

  // The `ComposerDraftApi` method facade (project surfaces only). A STABLE
  // object whose methods read the shell's LIVE draft/caret at call time — safe
  // to hand out once on mount because it never captures a frozen snapshot
  // (an adversarial review). `getValue` prefers the textarea's own value (the
  // freshest source) so a failure-restore CAS sees text typed after a clear.
  const draftApiRef = useRef<ComposerDraftApi | null>(null)
  if (draftApiRef.current === null) {
    draftApiRef.current = {
      getValue: () => projectComposerRef.current?.value ?? draftRef.current,
      getCaret: () =>
        projectComposerRef.current?.selectionStart ??
        (projectComposerRef.current?.value ?? draftRef.current).length,
      setValue: (text: string, caret?: number) => {
        setDraft(text)
        draftRef.current = text
        if (caret != null) pendingCaretRef.current = caret
      },
      onInputCapture: undefined,
    }
  }

  // Shell-owned scroll for project surfaces: `scrollToTurn` resolves a stable
  // per-turn `data-turn-id` scoped to THIS viewport (no-op-safe for a
  // not-loaded / not-found id, never throws); `scrollToBottom` scrolls the
  // viewport. Both are no-ops on main (the host owns main's scrolling via the
  // `refs` channel).
  useImperativeHandle(
    ref,
    (): ChatShellHandle => ({
      scrollToTurn: (turnId: string) => {
        if (isMain) return
        const vp = projectViewportRef.current
        if (!vp || turnId == null) return
        const escaped =
          typeof CSS !== "undefined" && typeof CSS.escape === "function"
            ? CSS.escape(String(turnId))
            : String(turnId).replace(/["\\]/g, "\\$&")
        const el = vp.querySelector(`[data-turn-id="${escaped}"]`)
        if (el) (el as HTMLElement).scrollIntoView({ block: "nearest" })
      },
      scrollToBottom: (behavior?: ScrollBehavior) => {
        if (isMain) return
        const vp = projectViewportRef.current
        if (!vp) return
        if (typeof vp.scrollTo === "function") vp.scrollTo({ top: vp.scrollHeight, behavior: behavior ?? "auto" })
        else vp.scrollTop = vp.scrollHeight
      },
    }),
    [isMain],
  )

  // Hand the draft API OUT once on mount, opt-in (group host wires it; private
  // does not, so private is unchanged). Main never constructs it.
  useEffect(() => {
    if (isMain) return
    if (onDraftApiReady && draftApiRef.current) onDraftApiReady(draftApiRef.current)
    // Once, on mount — the facade object is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isMain])

  // Esc-to-stop — project surfaces only (main keeps its own host listener).
  // FOLDED-IN fix (the one confirmed live bug): the shipped project Esc listener
  // called `stop` on ANY Escape, so pressing Esc to close a modal/drawer
  // silently cancelled a running private answer. Add main chat's two
  // long-standing guards:
  //  (a) OVERLAY-YIELD — a sibling overlay (`useEscapeToClose`) calls
  //      `preventDefault()` on a `document` listener that fires BEFORE this
  //      `window` listener, so `e.defaultPrevented` yields to it; and
  //  (b) BUSY-GATE — only stop when a turn is actually pending, so Esc with no
  //      generation running is a no-op REGARDLESS of overlays.
  // Esc with no overlay open still stops a generating answer (don't over-fix).
  useEffect(() => {
    if (isMain || !composer.escToStop || !composer.stop?.onStop) return
    const onStop = composer.stop.onStop
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      if (e.defaultPrevented) return
      if (!(turns as ShellTurn[]).some((t) => t.pending)) return
      onStop()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [isMain, composer.escToStop, composer.stop, turns])

  // Re-seat the textarea caret after a programmatic draft write (chip
  // insertion) — project surfaces only.
  useEffect(() => {
    if (isMain || pendingCaretRef.current === null) return
    const el = projectComposerRef.current
    const caret = pendingCaretRef.current
    pendingCaretRef.current = null
    if (el) {
      el.focus()
      try {
        el.setSelectionRange(caret, caret)
      } catch {
        /* jsdom/older engines may reject setSelectionRange on an unrendered value */
      }
    }
  }, [isMain, draft])

  // Pinned-follow auto-scroll — project surfaces only. On new turns, stick to
  // the bottom unless the reader scrolled up (parity with main's scroll feel).
  // Keyed on `frame.loading` too so the skeleton→transcript swap lands at the
  // true bottom rather than pinned to the top on first paint (an adversarial review).
  useEffect(() => {
    if (isMain) return
    const el = projectViewportRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [isMain, turns, frame.loading])

  const viewportBase = frame.viewportClassName ?? "od-center-scroll"

  // ── Project-surface path (surface !== "main") ─────────────────────────────
  if (!isMain) {
    const prefix = descriptor.testIdPrefix ?? ""
    const minChars = composer.minChars ?? DRAFT_MIN_CHARS
    const projectTurns = turns as ShellTurn[]
    const multiParty = !!transcript.multiParty
    // The composer-block derives from `busyMode` in ONE place governing BOTH
    // `ChatComposer.busy` and the submit guard. `never-block` (group) lets a
    // send fire while a reply is pending — the exact composer-lock bug the
    // pre-fold group surface fixed (a review). `block-while-asking`
    // (private) keeps blocking on a pending turn.
    const pendingExists = projectTurns.some((t) => t.pending)
    const blocked = composer.busyMode === "never-block" ? false : pendingExists
    // The additive composer feature bag. ABSENT → the previously
    // hard-stubbed inert defaults, byte-for-byte (attachments `[]`, `pinnedSkill`
    // null, no-op handlers) — a ledgered opt-out, never a live dead button.
    const f = composer.features

    // A single group row (self=me / peer=other / agent) maps onto ONE
    // ChatBubble via the descriptor: the shell owns the per-kind wrapper class
    // + `gc-msg-*` testid + human timestamp + role chip + avatar + invoked-by
    // badge (all from `ShellTurn.author` + engine-precomputed
    // `invokedBy`/`invokedByMe`), targeting SHELL-owned classes that survive
    // T3b's `ProjectGroupChat.module.css` delete. Bodies (MentionBubble /
    // AskReplyBody) arrive via the host's `renderUserBody`/`renderAgentBody`.
    const mapMultiParty = (turn: ShellTurn): ChatTranscriptTurn => {
      const time = turn.createdAt != null ? formatShellTime(turn.createdAt) : null
      if (turn.author.kind === "agent") {
        return {
          turnId: turn.id,
          dataTurnId: turn.id,
          dataTestId: "gc-msg-agent",
          wrapperClassName: `bc-turn ${shellStyles.gcMsg} ${shellStyles.gcMsgAgent}`,
          agentName: transcript.agentName,
          agentBadge: transcript.agentBadge ?? null,
          agentTimestamp: time,
          agentHeadExtra: transcript.turnHeadExtra?.(turn) ?? undefined,
          showAgent: true,
          agentBodyNode: transcript.renderAgentBody?.(turn),
          footer: transcript.turnFooter?.(turn) ?? undefined,
        }
      }
      const isMe = turn.author.kind === "self"
      return {
        turnId: turn.id,
        dataTurnId: turn.id,
        dataTestId: isMe ? "gc-msg-me" : "gc-msg-other",
        wrapperClassName: `bc-turn ${shellStyles.gcMsg} ${isMe ? shellStyles.gcMsgMe : shellStyles.gcMsgOther}`,
        showAgent: false,
        agentName: transcript.agentName,
        humanAlign: isMe ? "end" : "start",
        speaker: isMe ? "You" : (turn.author.name ?? "Someone"),
        userHeadExtra: (
          <>
            {!isMe && turn.author.role ? <span className={shellStyles.gcRole}>{turn.author.role}</span> : null}
            {time ? <span className={shellStyles.gcTime}>{time}</span> : null}
          </>
        ),
        user: {
          initials: turn.author.initials ?? null,
          // Only an inline-style OBJECT is applied (the engine feeds
          // `personAvatarStyle`); a bare-string placeholder is ignored.
          avatarStyle:
            turn.author.avatarStyle && typeof turn.author.avatarStyle === "object"
              ? turn.author.avatarStyle
              : undefined,
          bubbleClassName: isMe ? shellStyles.gcBubbleMe : shellStyles.gcBubbleOther,
          bodyNode: transcript.renderUserBody?.(turn),
        },
      }
    }

    // The single-party (private) mapping — behaviourally unchanged from T2; the
    // only addition is the `data-turn-id` scroll anchor.
    const mapSingleParty = (turn: ShellTurn): ChatTranscriptTurn => {
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

      const wrapperTestId =
        turn.author.kind === "agent" && prefix ? `${prefix}-history-agent` : undefined

      return {
        turnId: turn.id,
        dataTurnId: turn.id,
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
    }

    const mapped: ChatTranscriptTurn[] = projectTurns.map(multiParty ? mapMultiParty : mapSingleParty)

    // Backgrounded reply (group): the "stayed out / no reply yet" arm. Driven
    // by the last turn's frozen `runStatus` (today only its null arm renders);
    // undefined `reply.runStatus` (private) renders nothing.
    const lastTurn = projectTurns.length ? projectTurns[projectTurns.length - 1] : null
    const runStatusNode = reply.runStatus ? reply.runStatus(lastTurn?.runStatus ?? null, lastTurn) : null

    const submit = () => {
      const q = draft.trim()
      if (q.length < minChars || blocked) return
      send.onSubmit(q)
      setDraft("")
      draftRef.current = ""
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
                {frame.loading ? (
                  frame.loadingNode ?? null
                ) : (
                  <>
                    <ChatTranscript
                      turns={mapped}
                      leading={transcript.leading}
                      trailing={transcript.trailing}
                    />
                    {runStatusNode}
                  </>
                )}
              </div>
            </div>
          </div>
          <div className={frame.dockClassName ?? "bc-dock"}>
            {dock?.aboveComposer}
            <ChatComposer
              busy={blocked}
              draft={draft}
              pinnedSkill={f?.pinnedSkill ?? null}
              attachments={f?.attachments ?? []}
              hint={composer.hint ?? null}
              menuOpen={f?.menuOpen ?? false}
              menuActiveIndex={f?.menuActiveIndex ?? 0}
              slashMenu={composer.slashMenu ?? null}
              composerRef={projectComposerRef}
              fileInputRef={f?.fileInputRef ?? projectFileInputRef}
              onInput={(e) => {
                const value = e.target.value
                const caret = e.target.selectionStart ?? value.length
                // Surface hook (mention detection / typing broadcast) fires
                // with the REAL caret BEFORE the shell's own draft update.
                draftApiRef.current?.onInputCapture?.(value, caret)
                setDraft(value)
                draftRef.current = value
              }}
              onKeyDown={(e) => {
                // Picker keys outrank Enter-to-send: a `true` return means the
                // key was consumed → the shell neither submits nor stops.
                if (composer.onKeyDownCapture?.(e as unknown as KeyboardEvent)) return
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  submit()
                }
              }}
              onSend={submit}
              onStop={() => composer.stop?.onStop?.()}
              onToggleMenu={f?.onToggleMenu ?? (() => {})}
              onMenuActive={f?.onMenuActive ?? (() => {})}
              onMenuSelect={f?.onMenuSelect ?? (() => {})}
              onCloseMenu={f?.onCloseMenu ?? (() => {})}
              onRemoveAttachment={f?.onRemoveAttachment ?? (() => {})}
              onRemoveSkill={f?.onRemoveSkill ?? (() => {})}
              onFileSelect={f?.onFileSelect ?? (() => {})}
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
