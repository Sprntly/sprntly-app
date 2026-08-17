"use client"

// ── useMentionPicker — the @-mention people-picker engine ──
//
// The project-genuine picker half of the pre-fold `ProjectGroupChat`
// (`:315-479,:633-691,:853-877`), lifted into a reusable engine so the group
// surface can ride the shared `ChatShell`'s generic composer `slashMenu` slot.
// It owns the picker's own state (the active `@…` query, the debounced
// candidate search, the keyboard-nav active index, the post-select affordance),
// exposes the picker node + a key handler + the affordance row, and drives chip
// insertion + typing-target writes through the shell's `ComposerDraftApi`.
//
// Draft-API handoff (a review + a review — the "most important" gap):
// the picker does NOT receive the draft API as a construction argument. The
// shell creates that API but the picker is composed BEFORE `<ChatShell>`
// renders, so a direct pass would be circular. Instead the host hands the
// picker a STABLE `MutableRefObject<ComposerDraftApi | null>`; the picker's
// callbacks read `draftApiRef.current` LAZILY at call time (user types `@`,
// selects a member), by which point the shell has populated it on mount. The
// picker never captures the API object itself, only the ref.
//
// It does NOT own send-failure draft-restore — that lives in the send engine
// (`useProjectGroupThread`), which owns the compare-and-set (a review).
// Caret re-seat after a chip insertion is the shell's job now (the shell owns
// the textarea + the `pendingCaretRef` re-seat), driven by `setValue(text,
// caret)`; the picker only asks for the write.
import { createElement, useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AGENT_NAME } from "../../../../lib/agent"
import { projectsApi } from "../../../../lib/api"
import type { ComposerDraftApi } from "../../../shared/chat-shell/types"
import {
  detectMentionQuery,
  insertMentionChip,
  isEmailNeedle,
  parseMentionChips,
  type MentionQuery,
} from "./mentions"
import styles from "./mention-picker.module.css"

type CandidateRow = Awaited<ReturnType<typeof projectsApi.candidateSearch>>[number]
type TagResult = Awaited<ReturnType<typeof projectsApi.tagCandidate>>

type MentionMenuItem =
  | { row: "candidate"; kind: CandidateRow["kind"]; label: string; sublabel: string; needle: string }
  | { row: "invite"; label: string; needle: string }
  | { row: "agent"; label: string; needle: string }

type MentionAffordance = { tone: "ok" | "error"; text: string }

/** The email-send FAILED sentinel the backend returns (lowercase `"failed"`,
 *  per `backend/app/team_email.py`) — compared case-insensitively. */
function emailFailed(status: string | undefined): boolean {
  return (status ?? "").toLowerCase() === "failed"
}

/** Render human-bubble content with `@name` segments as presentational chips
 *  while text keeps its markdown. `@sprntly` is never chipped (the agent
 *  token). Absorbed from the pre-fold `ProjectGroupChat.MentionBubble`
 *  (`:141-162`) — exported for the group host's `renderUserBody` at fold. */
export function MentionBubble({ content }: { content: string }): ReactNode {
  const segments = parseMentionChips(content)
  return createElement(
    "span",
    { className: styles.mentionText },
    ...segments.map((seg, i) =>
      seg.type === "mention"
        ? createElement(
            "span",
            // The `gc-mention-chip` GLOBAL marker (alongside the hashed module
            // class) is a stable cross-module hook. The own-bubble AA override
            // it once carried is retired now that the own group bubble renders
            // in the shared light `bc-user-bubble` skin (no dark own bubble),
            // so the chip keeps its base `mention-picker.module.css` treatment.
            { key: i, className: `${styles.mentionChip} gc-mention-chip`, "data-testid": "gc-mention-chip" },
            `@${seg.label}`,
          )
        : createElement(
            ReactMarkdown,
            {
              key: i,
              remarkPlugins: [remarkGfm],
              components: { p: ({ children }: { children?: ReactNode }) => createElement("span", { className: styles.mentionText }, children) },
            },
            seg.value,
          ),
    ),
  )
}

export interface UseMentionPickerArgs {
  projectId: number | string
  /** The lazily-read draft API ref — populated by the shell on mount. */
  draftApiRef: MutableRefObject<ComposerDraftApi | null>
}

export interface UseMentionPicker {
  /** The picker list node, for the composer's generic `slashMenu` slot. Null
   *  when no `@…` token is active. */
  pickerNode: ReactNode | null
  /** Caret-aware input interception — the shell wires this to the draft API's
   *  `onInputCapture`, invoked with the REAL `selectionStart` before the
   *  shell's own draft update. Opens/updates the picker for an `@…` token. */
  handleComposerInput: (value: string, caret: number) => void
  /** Composer key handler for the composer's `onKeyDownCapture`: returns `true`
   *  when the picker consumed the key (arrow nav / Enter-selects / Escape-
   *  closes) so the shell neither submits nor stops. */
  handleKeys: (e: KeyboardEvent) => boolean
  /** The post-select affordance row (added / invite sent / re-invite hint). */
  affordanceRow: ReactNode | null
  /** True while a token is active — lets a host gate other composer chrome. */
  open: boolean
}

export function useMentionPicker({ projectId, draftApiRef }: UseMentionPickerArgs): UseMentionPicker {
  const [mentionQuery, setMentionQuery] = useState<MentionQuery | null>(null)
  const [candidates, setCandidates] = useState<CandidateRow[]>([])
  const [candLoading, setCandLoading] = useState(false)
  const [candError, setCandError] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [affordance, setAffordance] = useState<MentionAffordance | null>(null)

  const handleComposerInput = useCallback((value: string, caret: number) => {
    // Distinct-token detection: opens for an `@…` token that is NOT @sprntly
    // (the agent path); `@sprntly` returns null → no picker.
    const q = detectMentionQuery(value, caret)
    setMentionQuery(q)
    setActiveIndex(0)
  }, [])

  // Debounced candidate fetch for the active query — a rejected search degrades
  // to the in-menu error state, never throws during render.
  const activeQuery = mentionQuery?.query ?? null
  useEffect(() => {
    if (activeQuery === null) {
      setCandidates([])
      setCandLoading(false)
      setCandError(false)
      return
    }
    setCandLoading(true)
    setCandError(false)
    let cancelled = false
    const timer = setTimeout(() => {
      projectsApi
        .candidateSearch(projectId, activeQuery)
        .then((rows) => {
          if (cancelled) return
          setCandidates(rows)
          setCandLoading(false)
        })
        .catch(() => {
          if (cancelled) return
          setCandidates([])
          setCandError(true)
          setCandLoading(false)
        })
    }, 150)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [activeQuery, projectId])

  const mentionItems = useMemo<MentionMenuItem[]>(() => {
    if (mentionQuery === null) return []
    const q = mentionQuery.query
    const items: MentionMenuItem[] = []
    if (AGENT_NAME.toLowerCase().startsWith(q.toLowerCase())) {
      items.push({ row: "agent", label: AGENT_NAME, needle: AGENT_NAME })
    }
    items.push(
      ...candidates.map((c) => ({
        row: "candidate" as const,
        kind: c.kind,
        label: c.name ?? c.email ?? "Unknown",
        sublabel: c.email ?? "",
        needle: c.email ?? c.name ?? "",
      })),
    )
    const emailLike = isEmailNeedle(q)
    if (emailLike || (!candLoading && !candError && candidates.length === 0)) {
      items.push({
        row: "invite",
        needle: q,
        label: emailLike ? `Invite ${q} by email` : "No matches — invite by email",
      })
    }
    return items
  }, [mentionQuery, candidates, candLoading, candError])

  const closePicker = useCallback(() => {
    setMentionQuery(null)
    setActiveIndex(0)
  }, [])

  const handleMentionSelect = useCallback(
    (item: MentionMenuItem | undefined) => {
      if (!item || mentionQuery === null) return
      setAffordance(null)
      const api = draftApiRef.current

      // Agent OR existing-member row: insert the chip, NO network write. Splice
      // at the picker's TRACKED `mentionQuery` token (`start`/`end`), NOT the
      // live caret (Fable #4): a mouse-moved caret + Enter must not insert
      // mid-word and strand the `@token`. The live caret is only a fallback when
      // the tracked token no longer maps onto the current draft (the user edited
      // the text since it opened). The shell re-seats the caret from
      // `setValue(text, caret)`.
      if (item.row === "agent" || (item.row === "candidate" && item.kind === "member")) {
        if (api) {
          const cur = api.getValue()
          const q = mentionQuery
          const tokenStillMaps = q.end <= cur.length && cur.slice(q.start, q.end) === `@${q.query}`
          let text: string
          let newCaret: number
          if (tokenStillMaps) {
            const marker = `@${item.label} `
            text = cur.slice(0, q.start) + marker + cur.slice(q.end)
            newCaret = q.start + marker.length
          } else {
            const res = insertMentionChip(cur, api.getCaret(), item.label)
            text = res.text
            newCaret = res.caret
          }
          api.setValue(text, newCaret)
        }
        closePicker()
        return
      }

      // A non-member candidate or invite-by-email: tag them (AD-TNM6 — never
      // throw/block; a refuse degrades to generic copy).
      const label = item.row === "invite" ? item.needle : item.label
      closePicker()
      projectsApi
        .tagCandidate(projectId, item.needle)
        .then((raw) => {
          const res = raw as TagResult
          if (res.tier === "t_workspace") {
            setAffordance({ tone: "ok", text: `${label} added to the project` })
          } else if (res.tier === "t_company" || res.tier === "t_newuser") {
            if (emailFailed(res.email_status)) {
              setAffordance({
                tone: "error",
                text: `Invite created for ${label} — email didn't send; you can re-invite from Team settings`,
              })
            } else {
              setAffordance({ tone: "ok", text: `Invite sent to ${label}` })
            }
          } else {
            setAffordance({ tone: "ok", text: `${label} is already on the project` })
          }
        })
        .catch(() => {
          setAffordance({ tone: "error", text: "Couldn't add that person" })
        })
    },
    [draftApiRef, mentionQuery, projectId, closePicker],
  )

  const handleKeys = useCallback(
    (e: KeyboardEvent): boolean => {
      if (mentionQuery === null || mentionItems.length === 0) return false
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setActiveIndex((i) => (i + 1) % mentionItems.length)
        return true
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setActiveIndex((i) => (i - 1 + mentionItems.length) % mentionItems.length)
        return true
      }
      if (e.key === "Enter") {
        e.preventDefault()
        handleMentionSelect(mentionItems[activeIndex])
        return true
      }
      if (e.key === "Escape") {
        e.preventDefault()
        closePicker()
        return true
      }
      return false
    },
    [mentionQuery, mentionItems, activeIndex, handleMentionSelect, closePicker],
  )

  const pickerNode = useMemo<ReactNode | null>(() => {
    if (mentionQuery === null) return null
    let rows: ReactNode
    if (candLoading) {
      rows = createElement("div", { className: styles.pickerHint, "data-testid": "gc-mention-loading" }, "Searching…")
    } else if (candError) {
      rows = createElement(
        "div",
        { className: styles.pickerHint, "data-testid": "gc-mention-error" },
        "Couldn't search right now — keep typing to retry",
      )
    } else {
      rows = mentionItems.map((item, i) => {
        const key =
          item.row === "invite" ? `invite:${item.needle}` : item.row === "agent" ? "agent" : `${item.kind}:${item.needle}:${i}`
        const testid = item.row === "invite" ? "gc-mention-invite" : item.row === "agent" ? "gc-mention-agent" : "gc-mention-candidate"
        let inner: ReactNode
        if (item.row === "invite") {
          inner = createElement("span", { className: styles.pickerInvite }, item.label)
        } else if (item.row === "agent") {
          inner = [
            createElement("span", { key: "n", className: styles.pickerName }, item.label),
            createElement("span", { key: "k", className: styles.pickerKind, "data-kind": "agent" }, "Agent"),
          ]
        } else {
          inner = [
            createElement("span", { key: "n", className: styles.pickerName }, item.label),
            item.sublabel ? createElement("span", { key: "e", className: styles.pickerEmail }, item.sublabel) : null,
            createElement(
              "span",
              { key: "k", className: styles.pickerKind, "data-kind": item.kind, "data-testid": "gc-mention-kind" },
              item.kind === "member" ? "Member" : "Not on project",
            ),
          ]
        }
        return createElement(
          "button",
          {
            key,
            type: "button",
            role: "option",
            "aria-selected": i === activeIndex,
            className: `${styles.pickerRow}${i === activeIndex ? " " + styles.pickerRowActive : ""}`,
            "data-testid": testid,
            onMouseEnter: () => setActiveIndex(i),
            onMouseDown: (e: { preventDefault: () => void }) => e.preventDefault(),
            onClick: () => handleMentionSelect(item),
          },
          inner,
        )
      })
    }
    return createElement(
      "div",
      { className: styles.picker, role: "listbox", "aria-label": "Mention someone", "data-testid": "gc-mention-picker" },
      rows,
    )
  }, [mentionQuery, candLoading, candError, mentionItems, activeIndex, handleMentionSelect])

  const affordanceRow = useMemo<ReactNode | null>(() => {
    if (!affordance) return null
    return createElement(
      "div",
      {
        className: `${styles.affordance}${affordance.tone === "error" ? " " + styles.affordanceError : ""}`,
        role: "status",
        "data-testid": "gc-mention-affordance",
      },
      createElement("span", null, affordance.text),
    )
  }, [affordance])

  return { pickerNode, handleComposerInput, handleKeys, affordanceRow, open: mentionQuery !== null }
}
