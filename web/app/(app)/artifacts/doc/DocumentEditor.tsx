"use client"

import { useCallback, useEffect, useRef } from "react"
import { EditorContent, useEditor, type Editor } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import Link from "@tiptap/extension-link"
import { TableKit } from "@tiptap/extension-table"
import {
  BackgroundColor,
  Color,
  FontFamily,
  FontSize,
  TextStyle,
} from "@tiptap/extension-text-style"
import { isToolbarDriven } from "../../../lib/documentToolbarExec"
import {
  FONT_FAMILIES,
  FONT_SIZES,
  HEADING_LEVELS,
  HIGHLIGHT_COLORS,
  TEXT_COLORS,
  normalizeHref,
} from "./editorSchema"

// ── The document editor ──────────────────────────────────────────────────────
//
// A rich-text surface over one custom artifact: bold, italic, underline,
// headings, lists, quotes, code, links, and font family / size / colour.
//
// WHY TIPTAP RATHER THAN A CONTENTEDITABLE DIV. The rest of this app edits
// documents with a bare `contenteditable` (the PRD and Evidence panels do), and
// that works for surfaces whose editing is incidental. It does not survive real
// formatting: `document.execCommand` is deprecated and browser-divergent, and
// without a schema the DOM accumulates whatever a paste brings — nested fonts,
// styled divs, Word's XML — which the server's allowlist then strips on save,
// silently. TipTap parses INTO a schema, so a paste is normalized on the way in
// and what the user sees is what gets stored.
//
// THE SCHEMA IS BOUNDED BY THE SERVER'S SANITIZER, not by what TipTap offers.
// See editorSchema.ts: headings stop at 4 because the sanitizer keeps h1-h4,
// and there is no image button because `<img>` is not on its list. Anything the
// toolbar could produce that the sanitizer drops would be formatting that
// disappears on save with no error — the one failure mode this pairing exists
// to prevent.

export function DocumentEditor({
  initialHtml,
  editable = true,
  onChange,
  onBlur,
  onReady,
  hideToolbar = false,
}: {
  initialHtml: string
  editable?: boolean
  /** Fired on every change with the serialized document. The caller debounces —
   *  this is deliberately not throttled here, so the save layer owns the timing
   *  and can be tested without an editor (see lib/documentSave.ts).
   *
   *  NOT FIRED UNTIL A USER EVENT HAS LANDED ON THIS COMPONENT — see
   *  `interactedRef`. Stated as the real rule rather than the narrower "not
   *  fired for the editor's own normalization", because it also covers
   *  PROGRAMMATIC changes driven through the `Editor` handle this component
   *  hands out via `onReady`: those render but are not reported, and a caller
   *  adding, say, an "apply this suggestion" button outside the wrapper needs
   *  to know that before wondering why nothing saved. */
  onChange?: (html: string) => void
  onBlur?: () => void
  onReady?: (editor: Editor) => void
  /** Render no toolbar inside the editor.
   *
   *  The chat panel pins its own bar to the TOP of the panel — the posture the
   *  PRD already takes — so the controls stay put while the document scrolls
   *  under them. Inside the editor the bar scrolled away with the text, which
   *  is the report this closes. The full-page route keeps the built-in one:
   *  there the editor IS the page, so in-flow is where that bar belongs. */
  hideToolbar?: boolean
}) {
  // Has a person actually touched this editor yet?
  //
  // OPENING A DOCUMENT WAS SAVING IT. TipTap parses `initialHtml` into its
  // schema and re-serializes it, and that round trip is not byte-identical to
  // what the server stored (the stored HTML came from the sanitizer, which
  // makes different — equally valid — choices about attributes and spacing).
  // The resulting `onUpdate` looked exactly like a keystroke, so every open
  // scheduled a body save: on staging, opening document 9 twice took it from
  // version 3 to version 5 without anyone typing a character.
  //
  // In a SHARED library that is three separate harms. The row reads "Edited
  // just now" by whoever merely read it; `updated_by` names them; and the
  // version bump makes the next save by the colleague who is genuinely typing
  // fail its compare-and-set — a "someone else saved this document" banner
  // raised by a reader.
  //
  // AN UPDATE THAT FOLLOWS NO USER EVENT IS NOT A USER EDIT. Set by the
  // capture handlers on the wrapper below — every way a person can change this
  // document goes through one of them (typing, paste, a toolbar click, a
  // toolbar select, a drop), and the editor's own start-up normalization goes
  // through none.
  //
  // Two other instruments were tried and are worse:
  //   * TipTap's `onFocus` — does not fire for `chain().focus()` under jsdom,
  //     so it suppressed seven REAL toolbar edits in this component's own
  //     tests. A gate that drops genuine writing is worse than the save it
  //     prevents.
  //   * comparing serialized output against a baseline — needs a baseline
  //     captured before the first update, and `onCreate` does not reliably run
  //     first; it also silently drops a real edit that restores the original
  //     text.
  const interactedRef = useRef(false)
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const editor = useEditor({
    // Next renders this route on the server first; without this TipTap warns
    // about an SSR/client mismatch and re-mounts the document.
    immediatelyRender: false,
    // THE TOOLBAR MUST FOLLOW THE CARET. TipTap v3 defaults this to false, so
    // `useEditor` does not re-render on a transaction — and `Toolbar` reads
    // `editor.isActive(...)` during render. Without it, clicking into an
    // existing <h2> left the style select showing "Body"; choosing "Heading 2"
    // then TOGGLED THE HEADING OFF, turning it into a paragraph — the opposite
    // of what was asked. The same staleness made a picker unusable in the
    // other direction: a controlled <select> fires no change event for the
    // value it already displays, so re-applying a size the toolbar wrongly
    // believed was active did nothing.
    shouldRerenderOnTransaction: true,
    editable,
    extensions: [
      StarterKit.configure({
        // See editorSchema.ts — the sanitizer keeps h1-h4, so h5/h6 would be
        // unwrapped on save and come back as plain text.
        heading: { levels: [...HEADING_LEVELS] },
        // StarterKit ships its own Link in v3; ours is configured below, and
        // two Link marks in one schema is a duplicate-name error.
        link: false,
      }),
      // NO `Underline` HERE. StarterKit v3 ships one, and registering
      // `@tiptap/extension-underline` alongside it put a second mark of the
      // same name in the schema — TipTap warned "Duplicate extension names
      // found: ['underline']" on every single mount. Link above was disabled
      // for exactly this reason; underline was the one that got missed. The
      // button is unchanged: `toggleUnderline` comes from StarterKit's copy.
      TextStyle,
      FontFamily,
      FontSize,
      Color,
      BackgroundColor,
      // A TABLE IS CONTENT, AND A SCHEMA THAT LACKS IT DELETES IT SILENTLY.
      // ProseMirror parses `initialHtml` against this extension list and drops
      // every node it cannot place, KEEPING THE TEXT. Without the table nodes a
      // report's summary grid came back as one run-on paragraph — the cells of
      // "Theme | Accounts | Nature of signal" concatenated into
      // "ThemeAccountsNature of signal" with no separator, which reads as a
      // rendering bug and is really a parse loss. The report engines write
      // these grids constantly (RICE, prevalence counts, theme summaries), and
      // `report_markdown` converts them with markdown's `tables` extension, so
      // the HTML arriving here has always had real <table> markup in it.
      //
      // Worse than the display: `onUpdate` serializes what the schema kept, so
      // the first genuine keystroke anywhere in the document saved the
      // flattened body back over the stored one. The table was not just
      // invisible, it was one edit away from being gone.
      //
      // `resizable: false` because column widths are the one part of this the
      // server does NOT keep: the sanitizer allows `colspan`, `rowspan` and
      // `style` on cells and nothing else, so a dragged width would vanish on
      // save with no error — the same trap the heading levels avoid. Structure
      // survives the round trip; geometry was never ours to store.
      TableKit.configure({ table: { resizable: false } }),
      Link.configure({
        openOnClick: false,
        autolink: true,
        // Matches what the sanitizer will accept; anything else loses its href
        // server-side, so refusing it here keeps the two ends agreeing.
        protocols: ["http", "https", "mailto", "tel"],
        HTMLAttributes: { rel: "noopener noreferrer", target: "_blank" },
      }),
    ],
    content: initialHtml || "",
    onUpdate: ({ editor: ed }) => {
      // `isToolbarDriven` is the second half of this gate, and it exists
      // because the gate's listeners below only cover the editor's own
      // wrapper. Both panel hosts pin the formatting bar OUTSIDE that wrapper
      // (the report's is portalled into a slot elsewhere in the tree), so
      // clicking Bold there marked nothing: the text went bold, the status
      // pill still read "Saved", and the edit was gone on reopen. Every one of
      // those clicks goes through `execDocumentCommand`, which is where it is
      // now recorded.
      if (!interactedRef.current && !isToolbarDriven(ed)) return
      onChange?.(ed.getHTML())
    },
    onBlur: () => onBlur?.(),
  })

  // NATIVE listeners, not React's synthetic ones, and this list is the whole
  // correctness argument — a path that is missing here is an edit that renders
  // and is never saved, which is strictly worse than the spurious save this
  // gate exists to stop.
  //
  // `beforeinput` is the load-bearing one: it is the single event that covers
  // spellcheck corrections from the context menu, dictation, IME composition,
  // and Android suggestion-strip insertions — none of which fire `keydown`.
  // `cut` covers the context-menu Cut/Delete, which fires no `click` either
  // (a secondary-button press produces mousedown/contextmenu/mouseup only).
  // React's synthetic `onBeforeInput` is unreliable across browsers, which is
  // why these are attached to the DOM node directly.
  //
  // Missing any of them is not a theoretical gap: a colleague who right-clicks
  // a typo and picks the correction would watch the fix appear, close the tab,
  // and find the typo still there — with the save indicator never leaving
  // `idle`, so nothing warned them. It would also leave `bodyDirtyRef` false
  // in both consumers, so a later "Keep mine" would discard those edits.
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    const mark = () => { interactedRef.current = true }
    const EVENTS = [
      "keydown", "beforeinput", "input", "paste", "cut",
      "drop", "compositionstart", "click", "change",
    ]
    // Capture, so the mark lands before TipTap's own handler turns the event
    // into a transaction — otherwise the resulting `onUpdate` would still read
    // `interactedRef` as false and be swallowed.
    EVENTS.forEach((e) => el.addEventListener(e, mark, true))
    return () => EVENTS.forEach((e) => el.removeEventListener(e, mark, true))
  }, [editor])

  useEffect(() => {
    if (editor && onReady) onReady(editor)
  }, [editor, onReady])

  useEffect(() => {
    if (editor) editor.setEditable(editable)
  }, [editor, editable])

  if (!editor) return null

  return (
    <div
      data-doc-editor
      ref={wrapperRef}
    >
      {editable && !hideToolbar && <Toolbar editor={editor} />}
      <EditorContent editor={editor} />
      <style>{`
        [data-doc-editor] .tiptap { outline: none; min-height: 320px; }
        [data-doc-editor] .tiptap:focus { outline: none; }
        [data-doc-editor] .tiptap p { margin: 0 0 0.85em; }
        [data-doc-editor] .tiptap h1 { font-size: 1.9em; margin: 1.1em 0 0.4em; }
        [data-doc-editor] .tiptap h2 { font-size: 1.45em; margin: 1em 0 0.35em; }
        [data-doc-editor] .tiptap h3 { font-size: 1.2em; margin: 0.9em 0 0.3em; }
        [data-doc-editor] .tiptap h4 { font-size: 1.05em; margin: 0.85em 0 0.3em; }
        [data-doc-editor] .tiptap ul,
        [data-doc-editor] .tiptap ol { padding-left: 1.4em; margin: 0 0 0.85em; }
        [data-doc-editor] .tiptap blockquote {
          border-left: 3px solid var(--line, #E8E6E0);
          padding-left: 14px; margin: 0 0 0.85em; color: var(--ink-2, #5A5853);
        }
        [data-doc-editor] .tiptap pre {
          background: var(--surface-2, #F4F1EA); border-radius: 8px;
          padding: 12px 14px; overflow-x: auto;
        }
        [data-doc-editor] .tiptap a { color: var(--accent, #179463); }
        /* Tables. The browser's default is a borderless grid that reads as
           columns of loose text, so the rules that matter are the separators
           and the header weight — the same treatment the docs site gives a
           table, scoped here rather than shared because that stylesheet is the
           marketing site's. The default auto layout is left alone: a report
           grid has one long prose column and several short ones, and a fixed
           layout would give them equal widths. */
        [data-doc-editor] .tiptap table {
          border-collapse: collapse;
          width: 100%;
          margin: 0 0 0.85em;
          font-size: 0.95em;
        }
        [data-doc-editor] .tiptap th,
        [data-doc-editor] .tiptap td {
          border: 1px solid var(--line, #E8E6E0);
          padding: 8px 10px;
          text-align: left;
          vertical-align: top;
        }
        [data-doc-editor] .tiptap th {
          background: var(--surface-2, #F4F1EA);
          font-weight: 600;
        }
        /* The cell the caret is in, while editing. TipTap marks a multi-cell
           selection with this class; without a rule the selection is invisible
           and a cell-wide delete looks like it did nothing. */
        [data-doc-editor] .tiptap .selectedCell {
          background: var(--accent-alpha-10, rgba(23, 148, 99, 0.10));
        }
        /* NO placeholder rule here on purpose. The obvious one styles
           p.is-editor-empty::before with attr(data-placeholder), which only
           works with @tiptap/extension-placeholder — not installed, so the
           class and the attribute are never produced and the rule is dead CSS
           pretending to be an empty state. The route renders a real hint above
           the editor instead (see DocumentRoute's empty case): no extra
           dependency, and visible to a test. */
      `}</style>
    </div>
  )
}

// ── Toolbar ──────────────────────────────────────────────────────────────────

function Toolbar({ editor }: { editor: Editor }) {
  const setLink = useCallback(() => {
    const previous = editor.getAttributes("link").href as string | undefined
    // `window.prompt` rather than a custom modal: a link box is the one piece
    // of chrome that must not steal the selection, and a native prompt cannot.
    // A richer popover is worth building when this surface has a second dialog
    // to share the pattern with.
    const raw = window.prompt("Link URL", previous ?? "")
    if (raw === null) return
    if (raw.trim() === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run()
      return
    }
    const href = normalizeHref(raw)
    if (!href) {
      // Said out loud rather than silently dropped — the server would strip an
      // unsafe href on save and the user would never learn why.
      window.alert("That link was not added: only http, https, mailto and tel links are allowed.")
      return
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href }).run()
  }, [editor])

  return (
    <div data-doc-toolbar style={S.bar} role="toolbar" aria-label="Formatting">
      <Btn ed={editor} name="bold" label="B" title="Bold"
           style={{ fontWeight: 800 }}
           run={() => editor.chain().focus().toggleBold().run()} />
      <Btn ed={editor} name="italic" label="I" title="Italic"
           style={{ fontStyle: "italic" }}
           run={() => editor.chain().focus().toggleItalic().run()} />
      <Btn ed={editor} name="underline" label="U" title="Underline"
           style={{ textDecoration: "underline" }}
           run={() => editor.chain().focus().toggleUnderline().run()} />
      <Btn ed={editor} name="strike" label="S" title="Strikethrough"
           style={{ textDecoration: "line-through" }}
           run={() => editor.chain().focus().toggleStrike().run()} />

      <Sep />

      <Select
        testId="doc-heading"
        aria-label="Paragraph style"
        value={headingValue(editor)}
        onChange={(v) => {
          const chain = editor.chain().focus()
          if (v === "p") chain.setParagraph().run()
          else chain.toggleHeading({ level: Number(v) as 1 | 2 | 3 | 4 }).run()
        }}
        options={[
          { label: "Body", value: "p" },
          ...HEADING_LEVELS.map((l) => ({ label: `Heading ${l}`, value: String(l) })),
        ]}
      />
      <Select
        testId="doc-font"
        aria-label="Font"
        value={(editor.getAttributes("textStyle").fontFamily as string) || ""}
        onChange={(v) =>
          v
            ? editor.chain().focus().setFontFamily(v).run()
            : editor.chain().focus().unsetFontFamily().run()
        }
        options={FONT_FAMILIES}
      />
      <Select
        testId="doc-size"
        aria-label="Font size"
        value={(editor.getAttributes("textStyle").fontSize as string) || ""}
        onChange={(v) =>
          v
            ? editor.chain().focus().setFontSize(v).run()
            : editor.chain().focus().unsetFontSize().run()
        }
        options={FONT_SIZES}
      />
      <Select
        testId="doc-color"
        aria-label="Text colour"
        value={(editor.getAttributes("textStyle").color as string) || ""}
        onChange={(v) =>
          v ? editor.chain().focus().setColor(v).run() : editor.chain().focus().unsetColor().run()
        }
        options={TEXT_COLORS}
      />
      <Select
        testId="doc-highlight"
        aria-label="Highlight"
        value={(editor.getAttributes("textStyle").backgroundColor as string) || ""}
        onChange={(v) =>
          v
            ? editor.chain().focus().setBackgroundColor(v).run()
            : editor.chain().focus().unsetBackgroundColor().run()
        }
        options={HIGHLIGHT_COLORS}
      />

      <Sep />

      <Btn ed={editor} name="bulletList" label="• List" title="Bulleted list"
           run={() => editor.chain().focus().toggleBulletList().run()} />
      <Btn ed={editor} name="orderedList" label="1. List" title="Numbered list"
           run={() => editor.chain().focus().toggleOrderedList().run()} />
      <Btn ed={editor} name="blockquote" label="&ldquo;" title="Quote"
           run={() => editor.chain().focus().toggleBlockquote().run()} />
      <Btn ed={editor} name="codeBlock" label="&lt;/&gt;" title="Code block"
           run={() => editor.chain().focus().toggleCodeBlock().run()} />

      <Sep />

      <Btn ed={editor} name="link" label="Link" title="Add or edit a link" run={setLink} />
      <button
        type="button"
        data-testid="doc-clear-format"
        title="Clear formatting"
        onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}
        style={S.btn}
      >
        Clear
      </button>
    </div>
  )
}

function headingValue(editor: Editor): string {
  for (const level of HEADING_LEVELS) {
    if (editor.isActive("heading", { level })) return String(level)
  }
  return "p"
}

function Btn({
  ed, name, label, title, run, style,
}: {
  ed: Editor
  name: string
  label: string
  title: string
  run: () => void
  style?: React.CSSProperties
}) {
  const active = ed.isActive(name)
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      data-testid={`doc-${name}`}
      data-active={active ? "true" : "false"}
      // The editor loses focus (and therefore the selection) on mousedown
      // unless it is prevented — the selection IS the argument to every command
      // in this bar, so without this the buttons apply to nothing.
      onMouseDown={(e) => e.preventDefault()}
      onClick={run}
      style={{
        ...S.btn,
        ...style,
        background: active ? "var(--accent-alpha-10, rgba(23,148,99,0.10))" : "transparent",
        color: active ? "var(--accent, #179463)" : "var(--ink, #1A1A17)",
      }}
      dangerouslySetInnerHTML={{ __html: label }}
    />
  )
}

function Select({
  value, onChange, options, testId, ...rest
}: {
  value: string
  onChange: (v: string) => void
  options: { label: string; value: string }[]
  testId: string
  "aria-label": string
}) {
  return (
    <select
      data-testid={testId}
      value={value}
      onMouseDown={(e) => e.stopPropagation()}
      onChange={(e) => onChange(e.target.value)}
      style={S.select}
      {...rest}
    >
      {options.map((o) => (
        <option key={o.value || "default"} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

function Sep() {
  return <span aria-hidden style={{ width: 1, height: 20, background: "var(--line, #E8E6E0)", margin: "0 4px" }} />
}

const S: Record<string, React.CSSProperties> = {
  bar: {
    position: "sticky", top: 0, zIndex: 2,
    display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap",
    padding: "8px 0 12px", marginBottom: 16,
    borderBottom: "1px solid var(--line, #E8E6E0)",
    background: "var(--surface, #fff)",
  },
  btn: {
    minWidth: 30, height: 30, padding: "0 8px", borderRadius: 6,
    border: "1px solid transparent", background: "transparent",
    cursor: "pointer", fontSize: 13, lineHeight: 1,
    color: "var(--ink, #1A1A17)",
  },
  select: {
    height: 30, borderRadius: 6, fontSize: 12.5, padding: "0 6px",
    border: "1px solid var(--line, #E8E6E0)", background: "var(--surface, #fff)",
    color: "var(--ink, #1A1A17)", cursor: "pointer",
  },
}
