import type { Editor } from "@tiptap/react"
import { indentSelection } from "../(app)/artifacts/doc/editorIndent"

/**
 * Run one `PrdToolbar` command against a TipTap document.
 *
 * The toolbar speaks execCommand — it was built for the PRD's `contenteditable`,
 * where `document.execCommand("bold")` is the whole implementation. The document
 * panel drives a SCHEMA-BACKED editor instead, so the same vocabulary has to be
 * translated into chains. One bar, two hosts, no second toolbar to keep in step:
 * the PRD's and the document's formatting controls are now the same control.
 *
 * EVERY COMMAND THE BAR CAN EMIT IS TRANSLATED HERE. Align, indent and table
 * used to be missing, and the panel passed them to the toolbar's `omit` set so
 * they never rendered — which meant one bar offered different controls
 * depending on which artifact was open. They now have extensions behind them
 * (`TextAlign`, `editorIndent`, `TableKit`), so nothing is omitted. If a
 * command is ever added to the bar without a translation here, add the
 * extension first: a silently-ignored command is a button that does nothing,
 * which this codebase has shipped once already and left a note about.
 *
 * Returns whether the command was handled, so a caller can tell "did nothing"
 * from "not mine" — the panel logs the difference rather than swallowing it.
 */
/** Nothing. Kept as the toolbar's `omit` contract — a host that genuinely
 *  cannot run a command should still leave it out rather than render it inert
 *  — but this editor now answers every command the bar emits. */
export const UNSUPPORTED_DOCUMENT_COMMANDS: ReadonlySet<string> = new Set()

/** Editors this bar has actually been used on.
 *
 * `DocumentEditor` ignores an update until a user event has landed on the
 * editor's own wrapper — the gate that stops merely OPENING a document from
 * saving a new version of it. Both panel hosts pin this toolbar OUTSIDE that
 * wrapper (the report's is portalled into a slot elsewhere in the tree
 * entirely), so a click here reaches none of those listeners. Formatting
 * applied, the status pill went on reading "Saved", and the edit was gone on
 * reopen — with nothing anywhere saying so. A toolbar command IS a user event;
 * this is how it says so.
 *
 * Sticky per editor, like the gate it feeds: undo and redo dispatch their own
 * transactions from the history plugin, so a per-transaction marker would let
 * exactly those two fall back through the gate.
 *
 * WEAK on purpose — a closed panel's editor stays collectable, and a second
 * document gets a fresh editor that starts closed again.
 */
const toolbarDriven = new WeakSet<Editor>()

/** Has the user run a toolbar command against this editor? Read by
 *  `DocumentEditor`'s update gate. */
export function isToolbarDriven(editor: Editor): boolean {
  return toolbarDriven.has(editor)
}

export function execDocumentCommand(
  editor: Editor,
  cmd: string,
  value?: string,
): boolean {
  // `.focus()` first on every chain: the toolbar deliberately suppresses
  // mousedown so the selection survives the click, and a chain that does not
  // restore focus applies the mark to an editor nobody is in.
  const chain = () => {
    toolbarDriven.add(editor)
    return editor.chain().focus()
  }
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
    case "insertTable":
      // 3x3 with a header row — the shape you almost always want, and the same
      // one the PRD's raw-HTML insert produces, so a table looks identical
      // whichever artifact it was made in.
      chain().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()
      return true
    case "justifyLeft":
    case "justifyCenter":
    case "justifyRight":
      chain().setTextAlign(cmd.replace("justify", "").toLowerCase()).run()
      return true
    case "indent":
      toolbarDriven.add(editor)
      return indentSelection(editor, 1)
    case "outdent":
      toolbarDriven.add(editor)
      return indentSelection(editor, -1)
    case "fontName":
      value
        ? chain().setFontFamily(value).run()
        : chain().unsetFontFamily().run()
      return true
    case "fontSize":
      value ? chain().setFontSize(value).run() : chain().unsetFontSize().run()
      return true
    case "foreColor":
      value ? chain().setColor(value).run() : chain().unsetColor().run()
      return true
    case "hiliteColor":
      value
        ? chain().setBackgroundColor(value).run()
        : chain().unsetBackgroundColor().run()
      return true
    default:
      return false
  }
}

/** The toolbar's Style menu: body / h1-h3 / quote / code. */
function setBlock(editor: Editor, value: string | undefined): boolean {
  toolbarDriven.add(editor)
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
