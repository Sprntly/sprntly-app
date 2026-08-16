"use client"

/**
 * The shared chat-composer controller — the ONE home for send-command
 * construction and project-surface composer feature state.
 *
 * Before this, the pinned-skill splice and the attachment extract/upload
 * ride-along lived inlined inside `ChatScreen.tsx`'s `submitAsk`/
 * `handleComposerSubmit`, and the project composers were hard-stubbed to no-ops.
 * This module defines that logic ONCE:
 *   • `spliceSkill` — the single pinned-skill splice rule (byte-for-byte
 *     `ChatScreen`'s original `pinnedSkill ? `${trigger} ${text}` : text`);
 *   • `resolveAttachmentRefs` — the per-attachment extract (client-text |
 *     pre-extracted | server markdown) + best-effort upload → `AttachmentRef[]`;
 *   • `buildSendCommand` — composes a normalized `SendCommand` (the producer the
 *     project engines call);
 *   • `useChatComposerController` — the project-surface hook that owns composer
 *     feature STATE (attachments, pinned skill, the `+` menu, the skill palette)
 *     and hands a built `SendCommand` to its engine adapter;
 *   • `renderRunStatus` — the FE agent run-status consume (the honest failure UI).
 *
 * Main stays a `spliceSkill`/`resolveAttachmentRefs` CALLER (keeping its own host
 * state + render pass, so its DOM is byte-identical); the project surfaces are
 * full `useChatComposerController` consumers.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
} from "react"
import { askApi, attachmentsApi, type SkillInfo } from "../../lib/api"
import { SlashSkillMenu } from "./SlashSkillMenu"
import { AssistantWaitState } from "./AssistantWaitState"
import { DRAFT_MIN_CHARS, type PinnedSkill } from "./ChatComposer"
import type {
  AgentRunStatus,
  AttachmentRef,
  ChatSurfaceKind,
  SendCommand,
  ShellTurn,
} from "./chat-shell/types"

/** An attachment as the composer holds it before send: `content` present for a
 *  client-read text file, empty (`file` kept) for a binary doc parsed on send. */
export type AttachmentInput = { name: string; content?: string; file?: File | null }

// ── The pinned-skill splice rule (owned here; nobody re-derives it) ──────────

/** The RIDDEN query a route sees. Byte-for-byte `ChatScreen`'s original rule. */
export function spliceSkill(
  pinnedSkill: { trigger: string } | null | undefined,
  text: string,
): string {
  return pinnedSkill ? `${pinnedSkill.trigger} ${text}` : text
}

// ── The attachment extract + upload core (owned here) ────────────────────────

/**
 * Per attachment, in parallel: (1) resolve its text ONCE — a client-read text
 * file uses its `content`; a pre-extracted doc (main's early planner extraction)
 * uses that; an unread binary doc is parsed server-side via `askApi.extractFile`
 * (`.markdown`, clamped 50k); (2) best-effort upload the ORIGINAL file via
 * `attachmentsApi.upload` so the chip can render/download it later. The upload is
 * best-effort — a rejection (or missing backend) yields `key: null`, the text is
 * preserved, and the command never throws. Order is preserved.
 */
export async function resolveAttachmentRefs(
  items: AttachmentInput[],
  opts?: { preExtracted?: (string | null)[] | null },
): Promise<AttachmentRef[]> {
  const preExtracted = opts?.preExtracted ?? null
  return Promise.all(
    items.map(async (a, idx) => {
      const [text, stored] = await Promise.all([
        a.content
          ? Promise.resolve(a.content)
          : preExtracted?.[idx] != null
          ? Promise.resolve(preExtracted[idx] as string)
          : a.file
          ? askApi.extractFile(a.file).then((r) => r.markdown.slice(0, 50000))
          : Promise.resolve(a.content ?? ""),
        a.file
          ? Promise.resolve().then(() => attachmentsApi.upload(a.file!)).catch(() => null)
          : Promise.resolve(null),
      ])
      return {
        name: a.name,
        content: text,
        key: stored?.key ?? null,
        mime: stored?.mime ?? null,
        size: stored?.size ?? null,
      }
    }),
  )
}

// ── The send-command producer ────────────────────────────────────────────────

/** Builds the normalized `SendCommand` (plus a convenience `.riddenText`) that a
 *  surface hands to its route. The one place the splice + attachment ride-along
 *  + `clientMessageId` mint + scope stamp live. */
export async function buildSendCommand(input: {
  text: string
  pinnedSkill?: PinnedSkill | null
  attachments?: AttachmentInput[]
  scope: { surface: ChatSurfaceKind; projectId?: number | null }
}): Promise<SendCommand & { riddenText: string }> {
  const attachments = input.attachments?.length
    ? await resolveAttachmentRefs(input.attachments)
    : undefined
  return {
    text: input.text,
    pinnedSkill: input.pinnedSkill
      ? { id: input.pinnedSkill.id, trigger: input.pinnedSkill.trigger, label: input.pinnedSkill.label }
      : null,
    attachments,
    clientMessageId: crypto.randomUUID(),
    scope: { surface: input.scope.surface, projectId: input.scope.projectId ?? null },
    riddenText: spliceSkill(input.pinnedSkill, input.text),
  }
}

// ── The project-surface controller hook ──────────────────────────────────────

export interface ChatComposerControllerConfig {
  scope: { surface: ChatSurfaceKind; projectId?: number | null }
  /** The engine adapter — receives the built command (`engine.send`/`engine.post`). */
  onCommand: (cmd: SendCommand) => void
  /** false/undefined → the features bag omits the attachment members. */
  attachmentsEnabled?: boolean
  /** false/undefined → the features bag omits the pinned-skill / palette members. */
  skillsEnabled?: boolean
}

/** The `composer.features` bag shape (mirrors `chat-shell/types.ts`). */
export interface ChatComposerFeatures {
  pinnedSkill: PinnedSkill | null
  onRemoveSkill: () => void
  attachments: { name: string }[]
  onFileSelect: (e: ChangeEvent<HTMLInputElement>) => void
  onRemoveAttachment: (index: number) => void
  menuOpen: boolean
  menuActiveIndex: number
  onToggleMenu: () => void
  onMenuActive: (index: number) => void
  onMenuSelect: (index: number) => void
  onCloseMenu: () => void
  fileInputRef?: React.RefObject<HTMLInputElement | null>
}

export interface ChatComposerController {
  /** The additive descriptor bag — `undefined` when the surface enables no
   *  capability (a fully gated surface, e.g. the group chat until its backend
   *  can carry attachments/skills). */
  features?: ChatComposerFeatures
  /** The skill-palette node for `descriptor.composer.slashMenu` (null unless the
   *  palette is open on a skills-enabled surface). */
  slashMenu: ReactNode
  /** `descriptor.composer.onKeyDownCapture` — consumes arrow/enter/escape while
   *  the palette is open so they don't reach Enter-to-send. */
  onKeyDownCapture: (e: KeyboardEvent) => boolean
  /** `descriptor.send.onSubmit` — reads the live feature state, builds the
   *  `SendCommand`, hands it to `onCommand`, then clears its own state. */
  submit: (draftText: string) => void
}

export function useChatComposerController(config: ChatComposerControllerConfig): ChatComposerController {
  const { onCommand, attachmentsEnabled = false, skillsEnabled = false } = config

  const [attachments, setAttachments] = useState<AttachmentInput[]>([])
  const [pinnedSkill, setPinnedSkill] = useState<PinnedSkill | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [menuActiveIndex, setMenuActiveIndex] = useState(0)
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [slashOpen, setSlashOpen] = useState(false)
  const [slashActive, setSlashActive] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Live mirrors so `submit`/`onKeyDownCapture` read current state without being
  // re-created (and re-wired) on every keystroke.
  const attachmentsRef = useRef(attachments); attachmentsRef.current = attachments
  const pinnedSkillRef = useRef(pinnedSkill); pinnedSkillRef.current = pinnedSkill
  const slashOpenRef = useRef(slashOpen); slashOpenRef.current = slashOpen
  const slashActiveRef = useRef(slashActive); slashActiveRef.current = slashActive
  const skillsRef = useRef(skills); skillsRef.current = skills
  const scopeRef = useRef(config.scope); scopeRef.current = config.scope
  const onCommandRef = useRef(onCommand); onCommandRef.current = onCommand

  // Load the skill palette on mount (skills-enabled surfaces only). A failure
  // leaves it empty — an empty palette, not a stale hardcoded list.
  useEffect(() => {
    if (!skillsEnabled) return
    askApi.skills().then((r) => setSkills(r.skills)).catch(() => setSkills([]))
  }, [skillsEnabled])

  const onFileSelect = useCallback((e: ChangeEvent<HTMLInputElement>) => {
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
        setAttachments((prev) => [...prev, { name: file.name, content: content.slice(0, 50000), file }])
      }
      reader.readAsText(file)
    })
    e.target.value = "" // reset so the same file can be re-selected
  }, [])

  const pinSkill = useCallback((skill: SkillInfo) => {
    setPinnedSkill({ id: skill.id, label: skill.label, trigger: skill.trigger })
    setSlashOpen(false)
  }, [])

  const onKeyDownCapture = useCallback((e: KeyboardEvent): boolean => {
    if (!slashOpenRef.current) return false
    const count = skillsRef.current.length
    if (e.key === "ArrowDown") { e.preventDefault(); setSlashActive((i) => (count ? (i + 1) % count : 0)); return true }
    if (e.key === "ArrowUp") { e.preventDefault(); setSlashActive((i) => (count ? (i - 1 + count) % count : 0)); return true }
    if (e.key === "Enter") { e.preventDefault(); const s = skillsRef.current[slashActiveRef.current]; if (s) pinSkill(s); return true }
    if (e.key === "Escape") { e.preventDefault(); setSlashOpen(false); return true }
    return false
  }, [pinSkill])

  const submit = useCallback((draftText: string) => {
    const text = draftText
    if (text.trim().length < DRAFT_MIN_CHARS) return
    void buildSendCommand({
      text,
      pinnedSkill: pinnedSkillRef.current,
      attachments: attachmentsRef.current,
      scope: scopeRef.current,
    }).then((cmd) => onCommandRef.current(cmd))
    // Clear feature state (the shell already cleared the draft).
    setPinnedSkill(null)
    setAttachments([])
    setMenuOpen(false)
    setSlashOpen(false)
  }, [])

  const slashMenu: ReactNode =
    slashOpen && skillsEnabled ? (
      <SlashSkillMenu
        skills={skills}
        activeIndex={slashActive}
        onSelect={pinSkill}
        onHover={setSlashActive}
        inset
      />
    ) : null

  const noop = useCallback(() => {}, [])

  const features: ChatComposerFeatures | undefined =
    attachmentsEnabled || skillsEnabled
      ? {
          pinnedSkill,
          onRemoveSkill: skillsEnabled ? () => setPinnedSkill(null) : noop,
          attachments,
          onFileSelect: attachmentsEnabled ? onFileSelect : noop,
          onRemoveAttachment: attachmentsEnabled
            ? (i: number) => setAttachments((p) => p.filter((_, idx) => idx !== i))
            : noop,
          menuOpen,
          menuActiveIndex,
          onToggleMenu: () => { setMenuActiveIndex(0); setMenuOpen((o) => !o) },
          onMenuActive: setMenuActiveIndex,
          onMenuSelect: (i: number) => {
            setMenuOpen(false)
            if (i === 0 && attachmentsEnabled) { fileInputRef.current?.click(); return }
            if (i === 1 && skillsEnabled) { setSlashActive(0); setSlashOpen(true) }
          },
          onCloseMenu: () => setMenuOpen(false),
          fileInputRef: attachmentsEnabled ? fileInputRef : undefined,
        }
      : undefined

  return { features, slashMenu, onKeyDownCapture, submit }
}

// ── FE agent run-status consume (the honest failure UI) ──────────────────────

/**
 * The honest failure UI. `failed` → an error treatment + a Retry (only when a
 * `retryRun` handler is provided — dark until the backend exposes retry);
 * `running`/`queued` → a
 * pending treatment; `declined` → a QUIET stay-out node (calm, no alarming dot),
 * visually distinct from `failed` — the honest replacement for the old lying
 * "Sprntly stayed out" pill; `done`/`null`/`undefined` → nothing (the reply is
 * the content). `prefix` scopes the testids ("gc" → `gc-stayed-out-quiet`).
 */
export function renderRunStatus(opts: {
  status: AgentRunStatus | null | undefined
  turn: ShellTurn | null
  prefix: string
  retryRun?: (turn: ShellTurn | null) => void
}): ReactNode {
  const { status, turn, prefix, retryRun } = opts
  if (status === "failed") {
    return (
      <div role="alert" className="bc-run-failed" data-testid={`${prefix}-run-failed`}>
        <span>That didn’t go through.</span>
        {retryRun ? (
          <button
            type="button"
            className="bc-run-retry"
            data-testid={`${prefix}-run-retry`}
            onClick={() => retryRun(turn)}
          >
            Retry
          </button>
        ) : null}
      </div>
    )
  }
  if (status === "running" || status === "queued") {
    return (
      <div className="bc-run-pending" data-testid={`${prefix}-run-pending`}>
        <AssistantWaitState compact />
      </div>
    )
  }
  if (status === "declined") {
    return (
      <div className="bc-run-quiet" data-testid={`${prefix}-stayed-out-quiet`}>
        Sprntly stayed out of this one.
      </div>
    )
  }
  return null
}
