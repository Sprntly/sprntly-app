"use client"

/**
 * The per-conversation composer state, extracted verbatim from `ChatScreen`: the
 * draft, attachments, the slash palette + pinned skill, the `+` menu, the
 * transient busy hint, dictation, and the optimistic pending-send bubble — plus
 * the self-contained handlers (dictation toggle, file attach, focus-next-frame).
 *
 * Part of the shared conversation unit: main calls it once (its active
 * conversation) and a project slot will call it for its single conversation, so
 * the composer is the same code on every surface — never re-derived.
 *
 * What stays with the HOST for now (all read this hook's state through its
 * return, so they are byte-unchanged): the skill-catalog + slash-filter wiring,
 * `handleComposerInput`/`handleComposerKeyDown`/`handleComposerSubmit` (they call
 * the host's send/intent path), and the composer effects that live at other
 * positions in the host (the quote-insert sizing, the slash-filter recompute, the
 * global Esc handler) — kept in place so effect-registration order is unchanged.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { DRAFT_MAX_CHARS, type PinnedSkill } from "../../shared/ChatComposer"
import { useSpeechInput } from "../../../lib/useSpeechInput"

// Auto-clear delay for the transient composer hint (was a ChatScreen module
// const; moved here with the hint state it belongs to).
const BUSY_HINT_MS = 6000

export interface UseComposerDeps {
  /** Toast surface (kept on `handleFileSelect`'s dependency list, matching the
   *  host's original callback identity). */
  showToast: (title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => void
}

export function useComposer({ showToast }: UseComposerDeps) {
  const [draft, setDraft] = useState("")
  /** Set when a highlighted passage was just quoted in, so the layout effect
   *  that sizes and focuses the composer knows to run — see that effect. */
  const quoteJustInsertedRef = useRef(false)
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
  const [showSlash, setShowSlash] = useState(false)
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
  // `file` is set for document formats (.pdf/.pptx/.docx/.doc): those can't be
  // inlined as text client-side. The File feeds the PRD-import command
  // ("import this as a PRD" → POST /v1/prd/import) or, for a plain question,
  // server-side text extraction at send time (POST /v1/ask/extract-file).
  const [attachments, setAttachments] = useState<{ name: string; content: string; file?: File }[]>([])
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

  return {
    draft, setDraft,
    quoteJustInsertedRef,
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
  }
}
