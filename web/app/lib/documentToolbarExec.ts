import type { Editor } from "@tiptap/react"

/**
 * Run one `PrdToolbar` command against a TipTap document.
 *
 * The toolbar speaks execCommand — it was built for the PRD's `contenteditable`,
 * where `document.execCommand("bold")` is the whole implementation. The document
 * panel drives a SCHEMA-BACKED editor instead, so the same vocabulary has to be
 * translated into chains. One bar, two hosts, no second toolbar to keep in step:
 * the PRD's and the document's formatting controls are now the same control.
 *
 * WHAT IS NOT HERE IS AS DELIBERATE AS WHAT IS. Align, indent and table have no
 * extension in this editor's schema (see `DocumentEditor`'s `extensions`), so
 * they are not translated — the panel passes them to the toolbar's `omit` set
 * and they never render. Adding them means adding the extension first; a
 * silently-ignored command would be a button that does nothing, which this
 * codebase has shipped once already and left a note about.
 *
 * Returns whether the command was handled, so a caller can tell "did nothing"
 * from "not mine" — the panel logs the difference rather than swallowing it.
 */
export const UNSUPPORTED_DOCUMENT_COMMANDS: ReadonlySet<string> = new Set([
  "indent",
  "outdent",
  "justifyLeft",
  "justifyCenter",
  "justifyRight",
  // The toolbar's table button inserts raw HTML, which a schema-backed editor
  // cannot accept without a Table extension.
  "insertHTML",
])

export function execDocumentCommand(
  editor: Editor,
  cmd: string,
  value?: string,
): boolean {
  // `.focus()` first on every chain: the toolbar deliberately suppresses
  // mousedown so the selection survives the click, and a chain that does not
  // restore focus applies the mark to an editor nobody is in.
  const chain = () => editor.chain().focus()
  switch (cmd) {
    case "undo":
      chain().undo().run()
      return true
    case "redo":
      chain().redo().run()
      return true
    case "bold":
      chain().toggleBold().run()
      return true
    case "italic":
      chain().toggleItalic().run()
      return true
    case "underline":
      chain().toggleUnderline().run()
      return true
    case "strikeThrough":
      chain().toggleStrike().run()
      return true
    case "insertUnorderedList":
      chain().toggleBulletList().run()
      return true
    case "insertOrderedList":
      chain().toggleOrderedList().run()
      return true
    case "insertHorizontalRule":
      chain().setHorizontalRule().run()
      return true
    case "removeFormat":
      // Marks AND block type: "clear formatting" on a heading that stays a
      // heading has not cleared the formatting the user was looking at.
      chain().unsetAllMarks().setParagraph().run()
      return true
    case "createLink": {
      const href = (value || "").trim()
      if (!href) return false
      // `extendMarkRange` so clicking inside an existing link re-targets the
      // WHOLE link rather than splitting it at the caret.
      chain().extendMarkRange("link").setLink({ href }).run()
      return true
    }
    case "unlink":
      chain().extendMarkRange("link").unsetLink().run()
      return true
    case "formatBlock":
      return setBlock(editor, value)
    default:
      return false
  }
}

/** The toolbar's Style menu: body / h1-h3 / quote / code. */
function setBlock(editor: Editor, value: string | undefined): boolean {
  const chain = editor.chain().focus()
  switch ((value || "").toLowerCase()) {
    case "p":
      chain.setParagraph().run()
      return true
    case "h1":
    case "h2":
    case "h3":
    case "h4":
      // `setNode`, not `toggleHeading`: the menu names an outcome ("Heading"),
      // so picking it while already in that heading must leave a heading —
      // toggling would drop the reader back to body text for choosing what they
      // already had.
      chain.setHeading({ level: Number((value as string)[1]) as 1 | 2 | 3 | 4 }).run()
      return true
    case "blockquote":
      chain.toggleBlockquote().run()
      return true
    case "pre":
      chain.toggleCodeBlock().run()
      return true
    default:
      return false
  }
}
