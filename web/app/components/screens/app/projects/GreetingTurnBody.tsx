"use client"

/**
 * The on-join greeting's lead + Show-more/less split, reimplemented in the
 * CURRENT project-chat rendering path.
 *
 * `project_join_greeting.py` builds the greeting as
 * `{lead}{MORE_MARKER}{rest}` — the visible gist, then everything else (the
 * rest of the summary, the group-chat digest, the artifact list, the roster,
 * "For you"). The component that split it — the deleted
 * `ProjectIndividualChat`'s `AgentTurnBody` — went away in the chat rewrite, so
 * the greeting started rendering through the shared reply ladder with the raw
 * `<!--more-->` marker inline (`AskReplyBody` runs react-markdown WITHOUT
 * rehype-raw, so the marker is not drawn as HTML and leaks as text). This
 * restores the split: the pre-marker text renders as the visible lead, with a
 * Show more/less toggle revealing the rest — the marker itself never renders in
 * either half. Reuses the retained `.showMore` CSS atom.
 *
 * Routed in ONLY for a greeting turn (a turn whose reply carries the marker),
 * via the project surface's `renderAgentBody` override — main chat never passes
 * it, so main rendering is untouched.
 */

import { useState } from "react"
import type { AskResponse } from "../../../../lib/api"
import { AskReplyBody } from "../../../shared/AskReplyBody"
import { MORE_MARKER } from "../../../shared/chat-shell/types"
import styles from "./project-chat-extras.module.css"

/** A reply carrying only the given prose — the shape `AskReplyBody` reads. The
 *  greeting is deterministic prose; the citation/jira/etc. slots are empty. */
function proseReply(answer: string): AskResponse {
  return {
    answer,
    sources: [],
    follow_ups: [],
    key_points: [],
    citations: [],
    confidence: 1,
    unanswered: "",
  } as AskResponse
}

export function GreetingTurnBody({ answer }: { answer: string }) {
  const [expanded, setExpanded] = useState(false)

  const markerIdx = answer.indexOf(MORE_MARKER)
  // Defensive: the mapper only routes a marker-bearing turn here, but a turn
  // with no marker still renders its whole body rather than nothing.
  const lead = (markerIdx >= 0 ? answer.slice(0, markerIdx) : answer).trimEnd()
  const rest = (markerIdx >= 0 ? answer.slice(markerIdx + MORE_MARKER.length) : "").trim()

  // One markdown block so the lead and the revealed rest read as continuous
  // prose (never a seam between two separately-rendered answers).
  const shown = expanded && rest ? `${lead}\n\n${rest}` : lead

  return (
    <>
      <AskReplyBody reply={proseReply(shown)} />
      {rest ? (
        <button
          type="button"
          className={styles.showMore}
          aria-expanded={expanded}
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </>
  )
}
