/**
 * Viewer-only truncation of the v3 HTML PRD's Evidence section.
 *
 * The `prd-author` skill renders Evidence as `<ul class="ev"><li>…</li>…</ul>`
 * (the `.ev`/`.evmeta`/`.evtype` classes come from the canonical server-injected
 * stylesheet, so the structure is stable). When a PRD carries more than
 * `EV_MAX_VISIBLE` evidence items the panel gets long and repetitive, so we show
 * only the top few and add a "View more evidence" link that jumps to the panel's
 * Evidence tab.
 *
 * CRITICAL — this is a PANEL-VIEW transform, never a document edit: the HTML PRD
 * autosaves whatever is in its (contenteditable) iframe, so anything we inject
 * MUST be stripped before the doc is serialized for persistence, exactly like
 * PrdHtmlView's panel-style override. Both injected artifacts carry sentinel ids
 * for that reason — `stripEvidenceTruncation` removes them from the clone the
 * save path serializes, so the STORED PRD keeps ALL of its evidence.
 */

/** Id of the injected "View more evidence" link (stripped before persist). */
export const EV_MORE_ID = "sprntly-ev-more"
/** Id of the injected truncation <style> (stripped before persist). */
export const EV_TRUNC_STYLE_ID = "sprntly-ev-trunc"
/** How many evidence items stay visible in the panel; the rest fold behind the link. */
export const EV_MAX_VISIBLE = 3

/**
 * If the PRD document's Evidence list has more than `EV_MAX_VISIBLE` items, hide
 * the overflow (viewer-only CSS) and insert a "View more evidence" link right
 * below the list, wired to `onViewMore` (the panel switches to the Evidence tab).
 *
 * Idempotent — re-running is a no-op once the link exists (guards a second
 * iframe `load`). Returns true when it injected the affordance, else false.
 *
 * The sandboxed iframe can't run its own scripts, but the parent (same-origin)
 * attaches this click handler — the same mechanism PrdHtmlView already uses for
 * its `input` autosave listener.
 */
export function applyEvidenceTruncation(doc: Document, onViewMore: () => void): boolean {
  const list = doc.querySelector("ul.ev")
  if (!list) return false
  if (list.querySelectorAll(":scope > li").length <= EV_MAX_VISIBLE) return false
  if (doc.getElementById(EV_MORE_ID)) return false // already applied

  const style = doc.createElement("style")
  style.id = EV_TRUNC_STYLE_ID
  style.textContent =
    `ul.ev > li:nth-child(n+${EV_MAX_VISIBLE + 1}){display:none!important}` +
    `#${EV_MORE_ID}{display:inline-block;margin:2px 0 6px;color:#1A6B47;` +
    `font-weight:600;font-size:13px;cursor:pointer;text-decoration:none}` +
    `#${EV_MORE_ID}:hover{text-decoration:underline}`
  ;(doc.head ?? doc.documentElement).appendChild(style)

  const link = doc.createElement("a")
  link.id = EV_MORE_ID
  link.href = "#"
  link.textContent = "View more evidence"
  // Non-editable so a click switches tabs instead of dropping a caret inside the
  // contenteditable PRD body.
  link.setAttribute("contenteditable", "false")
  link.addEventListener("click", (e) => {
    e.preventDefault()
    onViewMore()
  })
  list.insertAdjacentElement("afterend", link)
  return true
}

/**
 * Remove the viewer-only truncation artifacts (link + style) from a cloned
 * document root before it is serialized for persistence, so the stored PRD stays
 * byte-clean and keeps every evidence item. Safe to call when nothing was injected.
 */
export function stripEvidenceTruncation(root: ParentNode): void {
  root.querySelector(`#${EV_MORE_ID}`)?.remove()
  root.querySelector(`#${EV_TRUNC_STYLE_ID}`)?.remove()
}
