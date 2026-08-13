"use client"

import { useCallback, useEffect } from "react"
import { EditorContent, useEditor, type Editor } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"
import Underline from "@tiptap/extension-underline"
import Link from "@tiptap/extension-link"
import {
  BackgroundColor,
  Color,
  FontFamily,
  FontSize,
  TextStyle,
} from "@tiptap/extension-text-style"
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
}: {
  initialHtml: string
  editable?: boolean
  /** Fired on every change with the serialized document. The caller debounces —
   *  this is deliberately not throttled here, so the save layer owns the timing
   *  and can be tested without an editor (see lib/documentSave.ts). */
  onChange?: (html: string) => void
  onBlur?: () => void
  onReady?: (editor: Editor) => void
}) {
  const editor = useEditor({
    // Next renders this route on the server first; without this TipTap warns
    // about an SSR/client mismatch and re-mounts the document.
    immediatelyRender: false,
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
      Underline,
      TextStyle,
      FontFamily,
      FontSize,
      Color,
      BackgroundColor,
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
    onUpdate: ({ editor: ed }) => onChange?.(ed.getHTML()),
    onBlur: () => onBlur?.(),
  })

  useEffect(() => {
    if (editor && onReady) onReady(editor)
  }, [editor, onReady])

  useEffect(() => {
    if (editor) editor.setEditable(editable)
  }, [editor, editable])

  if (!editor) return null

  return (
    <div data-doc-editor>
      {editable && <Toolbar editor={editor} />}
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
        /* The empty-document prompt. A blank page with no cue reads as broken. */
        [data-doc-editor] .tiptap p.is-editor-empty:first-child::before {
          content: attr(data-placeholder);
          color: var(--ink-3, #8C8A84); float: left; height: 0; pointer-events: none;
        }
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
