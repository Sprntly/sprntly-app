"use client"

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ComponentType,
  type CSSProperties,
  type ReactNode,
} from "react"
import { prdApi } from "../../lib/api"
import type { PrdSaveStatus } from "./PrdHtmlView"
import {
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconRedo,
  IconUndo,
} from "./app-icons"
import {
  IconAlignCenter,
  IconAlignLeft,
  IconAlignRight,
  IconBlockquote,
  IconChevronDown,
  IconClearFormatting,
  IconCode,
  IconDots,
  IconH1,
  IconH2,
  IconH3,
  IconIndentDecrease,
  IconIndentIncrease,
  IconLetterP,
  IconLineDashed,
  IconListNumbers,
  IconHighlight,
  IconPalette,
  IconStrikethrough,
  IconTextSize,
  IconTypography,
  IconUnlink,
} from "@tabler/icons-react"

import { FONT_FAMILIES, FONT_SIZES } from "../../(app)/artifacts/doc/editorSchema"
import { ColorGrid } from "./ColorGrid"

export type { PrdSaveStatus } from "./PrdHtmlView"

export interface PrdMarkdownHandle {
  /** Force an immediate save of the current editable document. Throws on
   *  failure so a caller (the panel's "Save now") can surface a toast — matches
   *  the pre-extraction inline behavior. */
  save: () => Promise<void>
}

// Draft key mirrors the pre-extraction PrdPanelContent key EXACTLY (no scope
// suffix) so a main-chat draft recovers byte-for-byte. A second consumer (the
// project drawer) passes `draftScope` to get a DISTINCT key and never collide
// with the main-chat draft for the same prd_id.
const PRD_DRAFT_KEY = (prdId: number, scope?: string) =>
  scope ? `sprntly_prd_draft_${prdId}_${scope}` : `sprntly_prd_draft_${prdId}`
function loadDraft(prdId: number, scope?: string): string | null {
  try { return localStorage.getItem(PRD_DRAFT_KEY(prdId, scope)) } catch { return null }
}
function saveDraft(prdId: number, html: string, scope?: string) {
  try { localStorage.setItem(PRD_DRAFT_KEY(prdId, scope), html) } catch { /* ignore */ }
}

/** The execCommand formatting toolbar — undo/redo/bold/italic/underline/H1/H2/
 *  bullet-list/link/table + a live save-status pip. Extracted verbatim from
 *  PrdPanelContent so both consumers render the identical control. Exported so
 *  the panel can also render the DISABLED no-document variant it shows in the
 *  empty / generating states (unchanged pre-extraction behavior). */
/** The block formats the style menu offers. `formatBlock` takes a tag name, and
 *  every one of these is a tag the PRD stylesheet already styles. */
const BLOCK_FORMATS: MenuEntry[] = [
  { cmd: "formatBlock", value: "p", label: "Body", Icon: IconLetterP },
  { cmd: "formatBlock", value: "h1", label: "Title", Icon: IconH1 },
  { cmd: "formatBlock", value: "h2", label: "Heading", Icon: IconH2 },
  { cmd: "formatBlock", value: "h3", label: "Subheading", Icon: IconH3 },
  { cmd: "formatBlock", value: "blockquote", label: "Quote", Icon: IconBlockquote },
  { cmd: "formatBlock", value: "pre", label: "Code", Icon: IconCode },
]

/** Font family / size / text colour / highlight, as menus rather than the
 *  full-page editor's `<select>`s — this bar has never had a select in it, and
 *  a swatch reads faster than a colour's name.
 *
 *  Built from `editorSchema`'s lists, which are the SAME values the full-page
 *  editor offers and are each known to survive the server's CSS allowlist.
 *  Duplicating them here would be two palettes drifting apart in a codebase
 *  that already keeps the schema and the sanitizer side by side on purpose. */
const FONT_TOOLS: MenuEntry[] = FONT_FAMILIES.map((f) => ({
  cmd: "fontName", value: f.value, label: f.label, Icon: IconTypography,
}))

const SIZE_TOOLS: MenuEntry[] = FONT_SIZES.map((f) => ({
  cmd: "fontSize", value: f.value, label: f.label, Icon: IconTextSize,
}))

/**
 * The less-reached-for half of the toolbar, behind a "More" menu.
 *
 * The split is by FREQUENCY, not by category: the panel is narrow whenever the
 * artifact drawer is open, and a toolbar that simply wraps or clips pushes the
 * save status off the end — which is the one control in that bar that must
 * never be hidden, because it is the only thing telling anyone whether their
 * edit reached the server. So the row carries what people use constantly and
 * everything else is one click away, at any width.
 */
const OVERFLOW_TOOLS: MenuEntry[] = [
  { cmd: "strikeThrough", label: "Strikethrough", Icon: IconStrikethrough },
  { cmd: "outdent", label: "Decrease indent", Icon: IconIndentDecrease },
  { cmd: "indent", label: "Increase indent", Icon: IconIndentIncrease },
  { cmd: "justifyLeft", label: "Align left", Icon: IconAlignLeft },
  { cmd: "justifyCenter", label: "Align centre", Icon: IconAlignCenter },
  { cmd: "justifyRight", label: "Align right", Icon: IconAlignRight },
  { cmd: "unlink", label: "Remove link", Icon: IconUnlink },
  { cmd: "insertHorizontalRule", label: "Divider", Icon: IconLineDashed },
  { cmd: "removeFormat", label: "Clear formatting", Icon: IconClearFormatting },
]

//: `.prd-more-menu`'s own `min-width` in globals.css. Mirrored here because the
//: clamp below has to know how wide the menu will be BEFORE it is drawn, and a
//: measured width would need a layout pass the click cannot wait for.
const MENU_MIN_WIDTH = 178

//: Breathing room kept between an open menu and the window edge.
const MENU_EDGE_GAP = 8

/** One row of a toolbar dropdown: what it runs, what it says, what it shows.
 *
 *  `Icon` is the COMPONENT, never a rendered element. These arrays live at
 *  module scope, and module-level JSX evaluates at IMPORT time — before a
 *  classic-runtime test file has set its React global, which takes down every
 *  suite that transitively imports this module with "React is not defined".
 *  Same footgun `ChatBubble` and `ArtifactListCards` already carry notes on. */
/** Every dropdown in the bar. One `openMenu` value, so opening any of them
 *  closes whichever was open — several hanging over the document at once is
 *  the bug two independent booleans would have written. */
type MenuId = "style" | "font" | "size" | "color" | "highlight" | "more"

type MenuEntry = {
  cmd: string
  value?: string
  label: string
  Icon: ComponentType<{ size?: number; stroke?: number }>
}

/**
 * The PRD formatting toolbar — shared by the markdown editor below and, since
 * the HTML PRD gained one, by `PrdPanelContent`'s iframe view too. One control,
 * both document formats.
 *
 * Every button is a `document.execCommand`. That API is formally deprecated and
 * is still the only thing that edits a `contenteditable` selection across all
 * current browsers without pulling in an editor framework — and a framework
 * here would have to own the document, which the HTML PRD cannot allow (it
 * round-trips the model's own markup). So: deprecated, universally implemented,
 * and deliberate.
 *
 * Every control suppresses `mousedown`. Without that, pressing a button moves
 * focus out of the document and collapses the selection the command is supposed
 * to act on — the same reason the HTML PRD's `exec` re-focuses its iframe.
 */
export function PrdToolbar({ hasDoc, saveStatus, exec, omit, savedLabel }: {
  hasDoc: boolean
  saveStatus: PrdSaveStatus
  exec: (cmd: string, value?: string) => void
  /** Commands this HOST cannot run, left out of the bar entirely.
   *
   *  A contenteditable answers every execCommand there is; a schema-backed
   *  editor answers the ones its extensions implement. The document panel drives
   *  a TipTap document with no text-align, indent or table extension, so those
   *  entries would be buttons that visibly do nothing — the exact defect this
   *  file already carries a note about ("shipped with NO onClick — inert the
   *  whole time"). Omitted, not disabled: a disabled control still claims the
   *  feature exists somewhere. Empty/absent for the PRD, whose bar is unchanged. */
  omit?: ReadonlySet<string>
  /** What the status pill says once everything is written. Defaults to the
   *  PRD's "Saved · Draft" — a word that means something on a PRD and nothing
   *  on a report, which is not a draft of anything. */
  savedLabel?: string
}) {
  // "Draft" is the PRD's own word for its state and means something there. A
  // team document or a report is not a draft of anything, so those hosts pass
  // plain "Saved" rather than telling the reader their report is a draft.
  const statusLabel =
    saveStatus === "saving" ? "Saving…"
      : saveStatus === "unsaved" ? "Unsaved"
        : savedLabel ?? "Saved · Draft"
  const statusColor = saveStatus === "saving" ? "var(--accent)" : saveStatus === "unsaved" ? "var(--ink-3)" : "var(--accent)"
  // WHICH menu is open, not whether one is — opening either must close the
  // other, and two independent booleans would let both sit open at once.
  const [openMenu, setOpenMenu] = useState<MenuId | null>(null)
  // WHERE the open menu is drawn, taken from its trigger's own rect at the
  // moment it opens. The menu is `position: fixed` (see `.prd-more-menu` in
  // globals.css), so these are viewport coordinates and no scrolling ancestor
  // can clip it — the Style menu lives inside `.prd-tools-l`, which scrolls,
  // and an absolutely-positioned dropdown there was clipped away to nothing:
  // the button opened, the state flipped, and the screen showed no menu.
  const [menuPos, setMenuPos] = useState<CSSProperties | null>(null)
  const barRef = useRef<HTMLDivElement>(null)
  // The two menu lists, minus anything this host cannot run. Computed rather
  // than filtered inline so both the list and the "is the menu worth showing"
  // question read from one value.
  const overflow = omit ? OVERFLOW_TOOLS.filter((t) => !omit.has(t.cmd)) : OVERFLOW_TOOLS
  const supports = (cmd: string) => !omit?.has(cmd)

  // Click-away and Escape close the menu. Bound only while it is open, so the
  // toolbar adds no document listeners in its resting state.
  useEffect(() => {
    if (!openMenu) return
    const onDown = (e: MouseEvent) => {
      if (!barRef.current?.contains(e.target as Node)) setOpenMenu(null)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpenMenu(null)
    }
    // A fixed menu is positioned once and does not follow its trigger, so
    // anything that MOVES the trigger closes it rather than leaving it hanging
    // beside nothing: scrolling the tool row, scrolling the document under the
    // sticky bar, or resizing the panel. `capture` because the tool row's own
    // scroll event does not bubble.
    const onMove = () => setOpenMenu(null)
    document.addEventListener("mousedown", onDown)
    document.addEventListener("keydown", onKey)
    document.addEventListener("scroll", onMove, true)
    window.addEventListener("resize", onMove)
    return () => {
      document.removeEventListener("mousedown", onDown)
      document.removeEventListener("keydown", onKey)
      document.removeEventListener("scroll", onMove, true)
      window.removeEventListener("resize", onMove)
    }
  }, [openMenu])

  const Tool = ({ cmd, value, title, children, testId }: {
    cmd: string
    value?: string
    title: string
    children: ReactNode
    testId: string
  }) => (
    <button
      type="button"
      className="prd-tool"
      disabled={!hasDoc}
      title={title}
      aria-label={title}
      data-testid={testId}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => exec(cmd, value)}
    >
      {children}
    </button>
  )

  /** A toolbar dropdown: a trigger plus a list of icon + label rows. Both the
   *  style menu and the overflow menu are this, so they cannot drift apart in
   *  look or in close-behaviour. */
  /** The trigger + popover shell. `entries` renders a list of commands;
   *  `children` renders anything else — today the colour grid — so both kinds
   *  of menu open, close, and are positioned by the same code. */
  const ToolMenu = ({ id, trigger, title, entries, testId, children }: {
    id: MenuId
    trigger: ReactNode
    title: string
    entries?: MenuEntry[]
    testId: string
    children?: ReactNode
  }) => (
    <div className="prd-more">
      <button
        type="button"
        className="prd-tool"
        disabled={!hasDoc}
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={openMenu === id}
        data-testid={testId}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          // Measured here rather than in an effect so the menu has its
          // coordinates on the very first paint — an effect would draw it at
          // the top-left corner for a frame first.
          //
          // Both menus hang LEFT from their own trigger, which is what they
          // did before they were fixed. The clamp is new and is the one thing
          // fixed positioning costs: an absolutely-positioned menu that ran
          // past the right edge still belonged to the page and could be
          // scrolled to, while a fixed one off the viewport is simply gone.
          const r = e.currentTarget.getBoundingClientRect()
          setMenuPos({
            top: r.bottom + 6,
            left: Math.max(
              MENU_EDGE_GAP,
              Math.min(r.left, window.innerWidth - MENU_MIN_WIDTH - MENU_EDGE_GAP),
            ),
          })
          setOpenMenu((cur) => (cur === id ? null : id))
        }}
      >
        {trigger}
      </button>
      {openMenu === id && (
        <div
          className="prd-more-menu"
          role="menu"
          data-testid={`${testId}-menu`}
          style={menuPos ?? undefined}
        >
          {children}
          {(entries ?? []).map((t) => (
            <button
              key={`${t.cmd}:${t.value ?? ""}`}
              type="button"
              role="menuitem"
              className="prd-more-item"
              data-testid={`prd-more-${t.value ?? t.cmd}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { exec(t.cmd, t.value); setOpenMenu(null) }}
            >
              <span className="prd-more-icon" aria-hidden><t.Icon size={15} stroke={1.9} /></span>
              {t.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <div className="prd-toolbar" ref={barRef}>
      <div className="prd-tools-l">
        <Tool cmd="undo" title="Undo" testId="prd-tool-undo"><IconUndo size={16} /></Tool>
        <Tool cmd="redo" title="Redo" testId="prd-tool-redo"><IconRedo size={16} /></Tool>
        <div className="prd-tool-divider" />

        {/* Block style. A CUSTOM menu, not a native <select>: an <option>
            cannot render an icon, and these read far faster as glyphs than as
            the words "Heading" and "Subheading" stacked side by side. */}
        <ToolMenu
          id="style"
          title="Text style"
          testId="prd-tool-block"
          entries={BLOCK_FORMATS}
          trigger={<span className="prd-tool-styletrigger">Style<IconChevronDown size={13} stroke={2} /></span>}
        />
        <div className="prd-tool-divider" />

        {/* Typography. On this bar since the document editor's own bar had
            font/size/colour and this one did not — one control for every
            artifact means the same controls, not merely the same component. */}
        <ToolMenu
          id="font"
          title="Font"
          testId="prd-tool-font"
          entries={FONT_TOOLS}
          trigger={<IconTypography size={16} stroke={1.9} />}
        />
        <ToolMenu
          id="size"
          title="Text size"
          testId="prd-tool-size"
          entries={SIZE_TOOLS}
          trigger={<IconTextSize size={16} stroke={1.9} />}
        />
        <ToolMenu
          id="color"
          title="Text colour"
          testId="prd-tool-color"
          trigger={<IconPalette size={16} stroke={1.9} />}
        >
          <ColorGrid
            testId="prd-color-grid"
            clearLabel="Default"
            onPick={(value) => { exec("foreColor", value); setOpenMenu(null) }}
          />
        </ToolMenu>
        <ToolMenu
          id="highlight"
          title="Highlight"
          testId="prd-tool-highlight"
          trigger={<IconHighlight size={16} stroke={1.9} />}
        >
          <ColorGrid
            testId="prd-highlight-grid"
            clearLabel="None"
            onPick={(value) => { exec("hiliteColor", value); setOpenMenu(null) }}
          />
        </ToolMenu>
        <div className="prd-tool-divider" />

        <Tool cmd="bold" title="Bold" testId="prd-tool-bold"><strong>B</strong></Tool>
        <Tool cmd="italic" title="Italic" testId="prd-tool-italic"><em>I</em></Tool>
        <Tool cmd="underline" title="Underline" testId="prd-tool-underline"><u>U</u></Tool>
        <div className="prd-tool-divider" />

        <Tool cmd="insertUnorderedList" title="Bulleted list" testId="prd-tool-ul"><IconListBullet size={16} /></Tool>
        <Tool cmd="insertOrderedList" title="Numbered list" testId="prd-tool-ol"><IconListNumbers size={16} stroke={1.9} /></Tool>
        <div className="prd-tool-divider" />

        <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert link"
          aria-label="Insert link"
          data-testid="prd-tool-link"
          style={{ display: "inline-flex", alignItems: "center" }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => { const url = prompt("Enter URL"); if (url) exec("createLink", url) }}
        >
          <IconLinkInsert size={15} /><span style={{ marginLeft: 5 }}>Link</span>
        </button>
        {/* This button shipped with NO onClick — it has been inert the whole
            time. It now emits `insertTable`, a command BOTH hosts implement:
            the HTML PRD turns it into an `insertHTML` of a starter table (see
            PrdHtmlView), the schema-backed editor into a real `insertTable`.
            It used to emit the raw HTML directly, which only a contenteditable
            could take — which is why documents and reports had no table
            button at all. */}
        {supports("insertTable") && <button
          type="button"
          className="prd-tool"
          disabled={!hasDoc}
          title="Insert table"
          aria-label="Insert table"
          data-testid="prd-tool-table"
          style={{ display: "inline-flex", alignItems: "center" }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => exec("insertTable")}
        >
          <IconGrid size={15} /><span style={{ marginLeft: 5 }}>Table</span>
        </button>}

      </div>

      {/* Everything else, one click away at any panel width.
          OUTSIDE `.prd-tools-l` deliberately, and this is load-bearing: that
          row scrolls (`overflow-x: auto`) so no tool is unreachable when the
          panel is narrow, and an absolutely-positioned menu inside a scroll
          container is CLIPPED by it — the button opened and nothing appeared.
          Pinned out here beside the status, it also stays reachable without
          scrolling, which is what an overflow affordance is for. */}
      {overflow.length > 0 && (
        <ToolMenu
          id="more"
          title="More formatting"
          testId="prd-tool-more"
          entries={overflow}
          trigger={<IconDots size={16} stroke={1.9} />}
        />
      )}

      <div className="prd-status">
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: hasDoc ? statusColor : "var(--muted)", transition: "background 0.3s" }} />
        {hasDoc ? statusLabel : "No draft"}
      </div>
    </div>
  )
}

/**
 * The markdown PRD editor — a `contenteditable` document region + the
 * execCommand formatting toolbar — as ONE shared, content-agnostic primitive.
 * Extracted from `PrdPanelContent` (AD-P13b: same extraction mold as the HTML
 * editor `PrdHtmlView`) so the main-chat PRD tab and the project artifact
 * drawer edit markdown PRDs through the identical control.
 *
 * Editing model (unchanged from the pre-extraction inline editor): the rendered
 * `children` are edited natively in the contenteditable; on autosave (debounced)
 * or a forced save, the document is FLATTENED to `innerText` and persisted to
 * `payload_md`. A local draft (keyed on prdId) preserves in-progress edits
 * across a remount.
 *
 * `onSave` (AD-P13b — one editor, two consumers): the injected persist HANDLER,
 * called with the flattened text + title. OMITTED (every main-chat caller) →
 * `persist` keeps calling `prdApi.update` byte-for-byte as before; this prop is
 * purely additive. The PROJECT drawer injects a project-scoped, ★ cross-project-
 * IDOR-gated save (`projectsApi.savePrdContent`) so a project edit never writes
 * through the global cross-tenant-only path.
 *
 * `readOnly`: guest / view-only rendering — no toolbar, non-editable body, and
 * `persist` refuses to reach any write path (three independent stops, mirroring
 * PrdHtmlView).
 */
export const PrdMarkdownEditor = forwardRef<PrdMarkdownHandle, {
  prdId: number
  title: string
  /** The document body to render INSIDE the contenteditable region. The
   *  main-chat caller passes its parsed PrdSections tree; the project drawer
   *  passes the PRD's raw markdown source. */
  children: ReactNode
  /** Rendered between the toolbar and the editable body, OUTSIDE the
   *  contenteditable (e.g. the main-chat PRD summary strip). */
  beforeBody?: ReactNode
  onStatus?: (s: PrdSaveStatus) => void
  onSave?: (text: string, title: string) => Promise<void>
  /** Guest / view-only. Defaults false → existing editable behavior. */
  readOnly?: boolean
  /** Distinct-draft namespace for a second consumer (the drawer) so its draft
   *  never collides with the main-chat draft for the same prd_id. Omitted →
   *  the byte-identical main-chat key. */
  draftScope?: string
}>(function PrdMarkdownEditor({ prdId, title, children, beforeBody, onStatus, onSave, readOnly = false, draftScope }, ref) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saveStatus, setSaveStatus] = useState<PrdSaveStatus>("saved")
  const titleRef = useRef(title)
  titleRef.current = title

  const setStatus = useCallback((s: PrdSaveStatus) => {
    setSaveStatus(s)
    onStatus?.(s)
  }, [onStatus])

  // Recover an in-progress edit (draft innerHTML) over the freshly-rendered
  // children — unchanged from the pre-extraction load-draft effect.
  useEffect(() => {
    if (readOnly || !bodyRef.current) return
    const draft = loadDraft(prdId, draftScope)
    if (draft) bodyRef.current.innerHTML = draft
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prdId])

  const write = useCallback(async (text: string) => {
    // AD-P13b: an injected handler (the drawer's gated save) takes precedence;
    // absent it, the byte-for-byte pre-existing main-chat path runs unchanged.
    if (onSave) await onSave(text, titleRef.current)
    else await prdApi.update(prdId, { title: titleRef.current, payload_md: text })
  }, [onSave, prdId])

  const handleInput = useCallback(() => {
    if (readOnly) return
    setStatus("unsaved")
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      const el = bodyRef.current
      if (!el) return
      setStatus("saving")
      saveDraft(prdId, el.innerHTML, draftScope)
      const text = el.innerText || ""
      try { await write(text); setStatus("saved") }
      catch { setStatus("saved") }
    }, 2000)
  }, [readOnly, setStatus, prdId, draftScope, write])

  const exec = (cmd: string, value?: string) => {
    bodyRef.current?.focus()
    document.execCommand(cmd, false, value)
  }

  // Imperative save (the panel's "Save now"): re-throws on failure so the
  // caller can toast "Save failed" — matches the pre-extraction inline path.
  const persist = useCallback(async () => {
    if (readOnly) return
    const el = bodyRef.current
    if (!el) return
    setStatus("saving")
    saveDraft(prdId, el.innerHTML, draftScope)
    const text = el.innerText || ""
    try { await write(text); setStatus("saved") }
    catch (e) { setStatus("saved"); throw e }
  }, [readOnly, setStatus, prdId, draftScope, write])

  useImperativeHandle(ref, () => ({ save: persist }), [persist])

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current)
  }, [])

  return (
    <>
      {!readOnly && <PrdToolbar hasDoc saveStatus={saveStatus} exec={exec} />}
      {beforeBody}
      <div
        className="prd-body"
        contentEditable={!readOnly}
        spellCheck={false}
        suppressContentEditableWarning
        ref={bodyRef}
        onInput={readOnly ? undefined : handleInput}
      >
        {children}
      </div>
    </>
  )
})
