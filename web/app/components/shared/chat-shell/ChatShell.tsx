"use client"

/**
 * The chat shell. It owns the frame `<main>`, the scroll viewport, the thread
 * column, the transcript mount, the dock, and the attachment-overlay mount —
 * the layout the main chat surface shares — and renders it through a typed
 * `ChatSurfaceDescriptor`.
 *
 * The host keeps its render pass whole: the finished `ChatTranscriptTurn[]`
 * (produced by the host-called `mapMainTurns`), the pending-send bubble, and the
 * composer all arrive host-rendered; the shell only lays them out. Scroll
 * behaviour (pin tracking, jump effects, the ResizeObserver) stays host-side,
 * operating through the `refs` channel — the shell renders the nodes, it never
 * owns the scroll logic.
 */

import { forwardRef, useImperativeHandle, useRef, type ReactNode } from "react"
import { ChatTranscript, type ChatTranscriptTurn } from "../ChatTranscript"
import { SelectionReplyToolbar } from "../SelectionReplyToolbar"
import type { ChatShellHandle, ChatSurfaceDescriptor } from "./types"

export interface ChatShellProps {
  descriptor: ChatSurfaceDescriptor
  /** The finished, host-mapped `ChatTranscriptTurn[]` (`mapMainTurns` output),
   *  consumed byte-identically. */
  turns: ChatTranscriptTurn[]
  /** The optimistic pending-send bubble, host-rendered. */
  pendingSend?: ReactNode
  /** The composer, host-rendered (its dual-mode closure). */
  composerNode?: ReactNode
  /** The attachment overlay, host-rendered; gated by
   *  `descriptor.overlays.attachmentViewer`. */
  attachmentViewer?: ReactNode
  /** MAIN ONLY: the reader highlighted a passage of an answer and pressed
   *  Reply. The shell owns the selection toolbar; the COMPOSER is host-rendered
   *  on main, so the parked quote lives in the host's state — hence the
   *  callback. Unset renders no toolbar at all, so a host that hasn't opted in
   *  is unchanged. */
  onQuoteSelection?: (text: string) => void
}

function ChatShellInner(
  {
    descriptor,
    turns,
    pendingSend,
    composerNode,
    attachmentViewer,
    onQuoteSelection,
  }: ChatShellProps,
  ref: React.Ref<ChatShellHandle>,
) {
  const { frame, transcript, dock, overlays, refs } = descriptor
  const isThread = frame.mode === "thread"

  // The main thread column, mirrored into a ref object the selection toolbar
  // can read. `refs.contentColumnRef` is a CALLBACK ref owned by the host (its
  // ResizeObserver attaches through it), so the shell composes rather than
  // replaces it — both are called, and the host's scroll behaviour is untouched.
  const mainColumnRef = useRef<HTMLDivElement>(null)
  // The scroll handle is a no-op: the main host owns its scrolling through the
  // `refs` channel and never calls these, so they stay inert.
  useImperativeHandle(
    ref,
    (): ChatShellHandle => ({
      scrollToTurn: () => {},
      scrollToBottom: () => {},
    }),
    [],
  )

  const viewportBase = frame.viewportClassName ?? "od-center-scroll"

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
              <div
                className={frame.threadClassName ?? "bc-thread"}
                ref={(el) => {
                  mainColumnRef.current = el
                  refs?.contentColumnRef?.(el)
                }}
              >
                <ChatTranscript turns={turns as ChatTranscriptTurn[]} leading={transcript.leading} />
                {pendingSend}
              </div>
            </div>
          ) : (
            frame.landing
          )}
        </div>

        {/* Highlight-to-reply, scoped to the thread column (`contentColumnRef`)
            rather than the viewport, so a selection in the landing block or
            the dock is never mistaken for a quotable passage. Opt-in: a host
            that passes no `onQuoteSelection` renders no toolbar and no extra
            listeners, keeping main's DOM byte-identical. */}
        {isThread && onQuoteSelection ? (
          <SelectionReplyToolbar
            containerRef={mainColumnRef}
            onReply={onQuoteSelection}
          />
        ) : null}

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
