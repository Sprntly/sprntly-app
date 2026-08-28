// Indent / outdent for the document editor.
//
// TipTap ships no indent extension, and the two obvious ways to fake one are
// both wrong here:
//
//   * `blockquote`, which is what `document.execCommand("indent")` produces in
//     a contenteditable, is a QUOTE. It carries a left rule and muted text in
//     every stylesheet this app renders through, so indenting a paragraph would
//     restyle it as a pull-quote.
//   * a CSS class, which the server's sanitizer drops — `class` is not on the
//     allowlist for any tag.
//
// So indentation is a `margin-left` on the block itself, which IS on the
// sanitizer's CSS allowlist (added alongside this — see
// `backend/app/custom_artifact_html.py::_ALLOWED_CSS`). Anything the toolbar
// can produce that the sanitizer would drop is formatting that disappears on
// save with no error, and the pairing in `editorSchema.ts` exists to prevent
// exactly that.
//
// Inside a list, indent means something different and more useful: nest the
// item. That is what every editor does, it is what `<ul><li><ul>` is for, and
// the nesting survives the sanitizer as structure rather than as style.
import { Extension } from "@tiptap/core"
import type { Editor } from "@tiptap/react"

/** One press. Matches the 1.4em list padding closely enough at the document's
 *  base size that an indented paragraph lines up with list text. */
export const INDENT_STEP_PX = 24

/** Six presses. A ceiling because there is no horizontal scroll on a document
 *  page — past this the text would march off the right edge with no way back
 *  except outdenting blind. */
export const MAX_INDENT_PX = INDENT_STEP_PX * 6

/** The blocks that can carry an indent. Not list items (they nest instead),
 *  not table cells (the cell is the box), not code blocks (their padding is
 *  the block's own). */
export const INDENTABLE = ["paragraph", "heading"] as const

/** Reads and writes the indent as `margin-left` on the block, so it round-trips
 *  through stored HTML rather than living only in the editor's memory. */
export const Indent = Extension.create({
  name: "indent",

  addGlobalAttributes() {
    return [
      {
        types: [...INDENTABLE],
        attributes: {
          indent: {
            default: 0,
            parseHTML: (element) => {
              const px = parseInt(element.style.marginLeft || "0", 10)
              return Number.isFinite(px) && px > 0 ? Math.min(px, MAX_INDENT_PX) : 0
            },
            renderHTML: (attributes) =>
              attributes.indent
                ? { style: `margin-left: ${attributes.indent}px` }
                : {},
          },
        },
      },
    ]
  },
})

/**
 * Indent (`delta` +1) or outdent (`delta` -1) whatever the selection is in.
 *
 * Returns false when there is nothing to do — already flush left, already at
 * the ceiling, or a block that does not take an indent — so the caller can
 * tell "did nothing" from "not mine", the same contract
 * `execDocumentCommand` keeps.
 */
export function indentSelection(editor: Editor, delta: 1 | -1): boolean {
  const chain = () => editor.chain().focus()
  // A list item nests instead. `can()` answers false at the outermost level,
  // where lifting would leave the list entirely — that is the outdent case,
  // and it falls through to the margin below, which is a no-op on a list item
  // and so correctly does nothing.
  if (delta > 0 && editor.can().sinkListItem("listItem")) {
    return chain().sinkListItem("listItem").run()
  }
  if (delta < 0 && editor.can().liftListItem("listItem")) {
    return chain().liftListItem("listItem").run()
  }

  const type = editor.state.selection.$from.parent.type.name
  if (!(INDENTABLE as readonly string[]).includes(type)) return false

  const current = Number(editor.getAttributes(type).indent) || 0
  const next = Math.min(MAX_INDENT_PX, Math.max(0, current + delta * INDENT_STEP_PX))
  if (next === current) return false
  return chain().updateAttributes(type, { indent: next }).run()
}
