"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import type { Editor } from "@tiptap/react"
import { reportsApi } from "../../lib/api"
import { DocumentEditor } from "../../(app)/artifacts/doc/DocumentEditor"
import { PrdToolbar } from "./PrdMarkdownEditor"
import {
  UNSUPPORTED_DOCUMENT_COMMANDS,
  execDocumentCommand,
} from "../../lib/documentToolbarExec"
import type { PrdSaveStatus } from "./PrdHtmlView"

/** How long after the last keystroke the edit is written. Matches the PRD
 *  editor and the document tab — same panel, same expectation about when
 *  "Saved" appears. */
const SAVE_AFTER_MS = 2000

/**
 * A report, read and edited in the panel as the rich document it is.
 *
 * The same `DocumentEditor` a team document uses, under the same `PrdToolbar`
 * the PRD uses, pinned above the text rather than scrolling with it. One
 * editor and one bar across all three artifacts in this panel — a report that
 * behaved differently from the document beside it was the report this closes.
 *
 * The body is always HTML by the time it gets here: reports are captured as
 * HTML, and the rows written before that convert on the way out of the API
 * (`app/report_markdown.py`). Saving therefore writes HTML back, which is what
 * upgrades a legacy row the first time someone edits it.
 *
 * EDITABLE ON SIGHT, with no mode to enter first -- the way the PRD and the team
 * document already are. A report is read far more often than it is changed, but
 * a mode you have to ask for is a step between the reader and a typo they can
 * already see, and there is only ever one rendering of the document either way.
 */
export function ReportDocument({
  reportId,
  html,
  onSaved,
  toolbarSlot,
}: {
  reportId: number
  html: string
  /** The saved body, so the tab's copy stays in step — otherwise leaving edit
   *  mode would show the body this report was fetched with. */
  onSaved: (html: string) => void
  /** Where the formatting bar renders: a slot the TAB owns, at the very top of
   *  the panel — straight after the artifact tabs and ahead of the title.
   *
   *  A portal rather than a lifted component, because everything the bar reads
   *  (the live editor, the save status) belongs to this component. Hoisting the
   *  markup would mean hoisting the editor and the save loop with it, for a
   *  question that is only about where a div appears on screen.
   *
   *  Null on the first commit — the slot arrives through a ref callback, so it
   *  exists one render later — and the bar simply does not render until then. */
  toolbarSlot: HTMLElement | null
}) {
  const [editor, setEditor] = useState<Editor | null>(null)
  const [status, setStatus] = useState<PrdSaveStatus>("saved")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latest = useRef(html)
  const onSavedRef = useRef(onSaved)
  onSavedRef.current = onSaved

  const save = useCallback(async () => {
    setStatus("saving")
    try {
      await reportsApi.update(reportId, { html: latest.current })
      setStatus("saved")
      onSavedRef.current(latest.current)
    } catch {
      // The text is still on screen and still the newest thing typed, so the
      // next keystroke reschedules. "Unsaved" is the honest state — a failed
      // write reading "Saved" is the one lie this indicator cannot tell.
      setStatus("unsaved")
    }
  }, [reportId])

  const onChange = useCallback((next: string) => {
    latest.current = next
    setStatus("unsaved")
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => { void save() }, SAVE_AFTER_MS)
  }, [save])

  // A pending edit is written on unmount. Closing the panel mid-debounce is how
  // two seconds of typing would otherwise disappear.
  useEffect(() => () => {
    if (!timer.current) return
    clearTimeout(timer.current)
    void reportsApi.update(reportId, { html: latest.current }).catch(() => {})
  }, [reportId])

  return (
    <div data-testid="report-document">
      {toolbarSlot && createPortal(
        <PrdToolbar
          hasDoc={!!editor}
          saveStatus={status}
          // Not "Saved · Draft": that is the PRD's word for its own state, and a
          // report is not a draft of anything.
          savedLabel="Saved"
          omit={UNSUPPORTED_DOCUMENT_COMMANDS}
          exec={(cmd, value) => { if (editor) execDocumentCommand(editor, cmd, value) }}
        />,
        toolbarSlot,
      )}
      <DocumentEditor
        // Keyed on the report so opening another one from the list remounts the
        // editor on its body rather than leaving the previous document in place.
        key={reportId}
        initialHtml={html}
        editable
        onChange={onChange}
        onReady={setEditor}
        onBlur={() => {
          if (!timer.current) return
          clearTimeout(timer.current)
          timer.current = null
          void save()
        }}
        // The bar is pinned above instead — see above.
        hideToolbar
      />
    </div>
  )
}
