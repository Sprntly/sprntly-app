"use client"

// useMentionPicker — the group-chat composer's @-mention picker (project-only).
//
// Type `@` → a member-scoped candidate list (the shared `/candidates`
// directory), plus the `@Sprntly` agent option and an invite-by-email row.
// Selecting a member/agent inserts a chip locally (no network); selecting a
// non-member/email tags them onto the project (`/tag`) and reports via the
// injected `onAffordance` (a toast). The `@sprntly` word never opens the people
// picker as a person (mentions.ts owns the agent-token split).
//
// Fully project-local: driven by an adapter-supplied `ComposerDraftApi` built
// over the shared composer's textarea ref — it touches NO shared composer code.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react"
import { projectsApi } from "../../../../lib/api"
import { AGENT_NAME } from "../../../../lib/agent"
import {
  detectMentionQuery,
  insertMentionChip,
  isEmailNeedle,
  type MentionQuery,
} from "./mentions"
import styles from "./mentionPicker.module.css"

/** The minimal draft surface the picker reads/writes — built by the adapter
 *  over the shared composer's textarea ref + setDraft (no shared composer
 *  change). `setValue` re-seats the caret after the controlled update. */
export interface ComposerDraftApi {
  getValue(): string
  getCaret(): number
  setValue(text: string, caret?: number): void
}

type CandidateRow = Awaited<ReturnType<typeof projectsApi.candidateSearch>>[number]

type MentionMenuItem =
  | { row: "agent"; label: string; needle: string }
  | { row: "candidate"; kind: string; label: string; sublabel: string; needle: string }
  | { row: "invite"; label: string; needle: string }

export type MentionAffordance = { tone: "ok" | "error"; text: string }

export interface UseMentionPickerArgs {
  projectId: number | string
  draftApi: ComposerDraftApi
  /** Post-select status (member added / invite sent / failed) — routed to a
   *  toast by the host. */
  onAffordance?: (a: MentionAffordance) => void
}

export interface UseMentionPicker {
  /** The picker list node (for a project-local overlay). Null when no `@…`
   *  token is active. */
  pickerNode: ReactNode | null
  /** Caret-aware input interception — the adapter wraps the composer's
   *  onChange and calls this with the REAL selectionStart. */
  handleComposerInput: (value: string, caret: number) => void
  /** Key handler: returns true when the picker consumed the key (arrow nav /
   *  Enter-selects / Escape-closes) so the composer neither submits nor stops. */
  handleKeys: (e: { key: string; preventDefault: () => void }) => boolean
  /** True while a token is active. */
  open: boolean
}

function emailFailed(status: string | undefined): boolean {
  return (status ?? "").toLowerCase() === "failed"
}

export function useMentionPicker({ projectId, draftApi, onAffordance }: UseMentionPickerArgs): UseMentionPicker {
  const [mentionQuery, setMentionQuery] = useState<MentionQuery | null>(null)
  const [candidates, setCandidates] = useState<CandidateRow[]>([])
  const [candLoading, setCandLoading] = useState(false)
  const [candError, setCandError] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)

  const handleComposerInput = useCallback((value: string, caret: number) => {
    // Distinct-token detection: opens for an `@…` token that is NOT @sprntly
    // (agent path); `@sprntly` → null → no picker. Handles `@` mid-word (an
    // inline email like me@acme.com never opens the picker — mentions.ts).
    setMentionQuery(detectMentionQuery(value, caret))
    setActiveIndex(0)
  }, [])

  // Debounced member-scoped candidate fetch — a rejected search degrades to the
  // in-menu error state, never throws during render.
  const activeQuery = mentionQuery?.query ?? null
  useEffect(() => {
    if (activeQuery === null) {
      setCandidates([]); setCandLoading(false); setCandError(false)
      return
    }
    setCandLoading(true); setCandError(false)
    let cancelled = false
    const timer = setTimeout(() => {
      projectsApi
        .candidateSearch(projectId, activeQuery)
        .then((rows) => { if (!cancelled) { setCandidates(rows); setCandLoading(false) } })
        .catch(() => { if (!cancelled) { setCandidates([]); setCandError(true); setCandLoading(false) } })
    }, 150)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [activeQuery, projectId])

  const mentionItems = useMemo<MentionMenuItem[]>(() => {
    if (mentionQuery === null) return []
    const q = mentionQuery.query
    const items: MentionMenuItem[] = []
    // The agent option (@Sprntly) whenever the query prefixes it — this is the
    // #3 agent-tagging affordance; selecting it inserts `@Sprntly`.
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

  const closePicker = useCallback(() => { setMentionQuery(null); setActiveIndex(0) }, [])

  const handleMentionSelect = useCallback(
    (item: MentionMenuItem | undefined) => {
      if (!item || mentionQuery === null) return

      // Agent OR existing-member row: insert the chip, NO network write. Splice
      // at the TRACKED token (start/end), not the live caret — a mouse-moved
      // caret + Enter must not strand the `@token` mid-word. Fall back to the
      // live caret only when the tracked token no longer maps onto the draft.
      if (item.row === "agent" || (item.row === "candidate" && item.kind === "member")) {
        const cur = draftApi.getValue()
        const q = mentionQuery
        const tokenStillMaps = q.end <= cur.length && cur.slice(q.start, q.end) === `@${q.query}`
        if (tokenStillMaps) {
          const marker = `@${item.label} `
          draftApi.setValue(cur.slice(0, q.start) + marker + cur.slice(q.end), q.start + marker.length)
        } else {
          const res = insertMentionChip(cur, draftApi.getCaret(), item.label)
          draftApi.setValue(res.text, res.caret)
        }
        closePicker()
        return
      }

      // A non-member candidate or invite-by-email: tag them onto the project
      // (never throws/blocks; a refuse degrades to generic copy).
      const label = item.row === "invite" ? item.needle : item.label
      closePicker()
      projectsApi
        .tagCandidate(projectId, item.needle)
        .then((res) => {
          if (res.tier === "t_workspace") {
            onAffordance?.({ tone: "ok", text: `${label} added to the project` })
          } else if (res.tier === "t_company" || res.tier === "t_newuser") {
            onAffordance?.(
              emailFailed(res.email_status)
                ? { tone: "error", text: `Invite created for ${label} — email didn't send; re-invite from Team settings` }
                : { tone: "ok", text: `Invite sent to ${label}` },
            )
          } else {
            onAffordance?.({ tone: "ok", text: `${label} is already on the project` })
          }
        })
        .catch(() => { onAffordance?.({ tone: "error", text: "Couldn't add that person" }) })
    },
    [draftApi, mentionQuery, projectId, closePicker, onAffordance],
  )

  const handleKeys = useCallback(
    (e: { key: string; preventDefault: () => void }): boolean => {
      if (mentionQuery === null || mentionItems.length === 0) return false
      if (e.key === "ArrowDown") { e.preventDefault(); setActiveIndex((i) => (i + 1) % mentionItems.length); return true }
      if (e.key === "ArrowUp") { e.preventDefault(); setActiveIndex((i) => (i - 1 + mentionItems.length) % mentionItems.length); return true }
      if (e.key === "Enter") { e.preventDefault(); handleMentionSelect(mentionItems[activeIndex]); return true }
      if (e.key === "Escape") { e.preventDefault(); closePicker(); return true }
      return false
    },
    [mentionQuery, mentionItems, activeIndex, handleMentionSelect, closePicker],
  )

  const pickerNode = useMemo<ReactNode | null>(() => {
    if (mentionQuery === null) return null
    let rows: ReactNode
    if (candLoading) {
      rows = <div className={styles.pickerHint} data-testid="gc-mention-loading">Searching…</div>
    } else if (candError) {
      rows = <div className={styles.pickerHint} data-testid="gc-mention-error">Couldn&apos;t search right now — keep typing to retry</div>
    } else {
      rows = mentionItems.map((item, i) => {
        const key = item.row === "invite" ? `invite:${item.needle}` : item.row === "agent" ? "agent" : `${item.kind}:${item.needle}:${i}`
        const testid = item.row === "invite" ? "gc-mention-invite" : item.row === "agent" ? "gc-mention-agent" : "gc-mention-candidate"
        let inner: ReactNode
        if (item.row === "invite") {
          inner = <span className={styles.pickerInvite}>{item.label}</span>
        } else if (item.row === "agent") {
          inner = (
            <>
              <span className={styles.pickerName}>{item.label}</span>
              <span className={styles.pickerKind} data-kind="agent">Agent</span>
            </>
          )
        } else {
          inner = (
            <>
              <span className={styles.pickerName}>{item.label}</span>
              {item.sublabel ? <span className={styles.pickerEmail}>{item.sublabel}</span> : null}
              <span className={styles.pickerKind} data-kind={item.kind} data-testid="gc-mention-kind">
                {item.kind === "member" ? "Member" : "Not on project"}
              </span>
            </>
          )
        }
        return (
          <button
            key={key}
            type="button"
            role="option"
            aria-selected={i === activeIndex}
            className={`${styles.pickerRow}${i === activeIndex ? " " + styles.pickerRowActive : ""}`}
            data-testid={testid}
            onMouseEnter={() => setActiveIndex(i)}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => handleMentionSelect(item)}
          >
            {inner}
          </button>
        )
      })
    }
    return (
      <div className={styles.picker} role="listbox" aria-label="Mention someone" data-testid="gc-mention-picker">
        {rows}
      </div>
    )
  }, [mentionQuery, candLoading, candError, mentionItems, activeIndex, handleMentionSelect])

  return { pickerNode, handleComposerInput, handleKeys, open: mentionQuery !== null }
}
