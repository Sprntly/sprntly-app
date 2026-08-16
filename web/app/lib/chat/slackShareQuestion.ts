// What a share preview still needs to ask the user — as a QuestionPopup
// question.
//
// Every choice this product asks for goes through the QuestionPopup (owner's
// directive, 2026-08-16: the clarify gate, ticket assignment and now sharing
// all ask in the dock above the composer, never as a second picker inside a
// thread card). This is the mapping from "what the preview could not settle"
// to "what the user is asked", kept out of ChatScreen so it can be tested
// without a chat around it.

import type { SlackSharePreview } from "../api"

export type SlackShareQuestion = {
  /** "channel" — the answer is a channel NAME, which the host re-previews
   *  server-side so membership and the private-channel block are re-checked
   *  against the channel actually chosen. "target" — the answer is a
   *  `${type}-${id}` key into the preview's own candidates. */
  kind: "channel" | "target"
  header: string
  prompt: string
  options: { label: string; description?: string | null; value: string }[]
}

/** The question this preview still needs answered, or null when it needs
 *  nothing — it is ready, it is blocked, the kind cannot be shared, or there
 *  is nothing to choose between (in which case the card says so itself rather
 *  than opening an empty popup). */
export function slackShareQuestionFor(
  preview: SlackSharePreview,
): SlackShareQuestion | null {
  if (preview.status === "needs_channel") {
    const channels = preview.channels ?? []
    if (!channels.length) return null
    return {
      kind: "channel",
      header: "Slack channel",
      prompt: preview.channel_query
        ? `I couldn't find #${preview.channel_query} — which channel should this go to?`
        : "Which channel should this go to?",
      options: channels.map((c) => ({
        label: `#${c.name}`,
        // The join/can't-join state belongs on the option, not after the
        // pick: it is the difference between an expected bot join and one
        // that surprises a channel.
        description: c.is_member
          ? null
          : c.is_private
            ? "Sprntly isn't in this one and can't add itself"
            : "Sprntly will join to post",
        // The NAME, not the id — the host re-previews by name, and exact-name
        // matching is what re-runs the server's own channel checks.
        value: c.name,
      })),
    }
  }
  if (preview.status === "ambiguous_target") {
    const candidates = preview.candidates ?? []
    if (!candidates.length) return null
    return {
      kind: "target",
      header: "Which document",
      prompt: "Which one do you want to share?",
      options: candidates.map((c) => ({
        label: c.title,
        description: c.kind_label,
        value: `${c.type}-${c.id}`,
      })),
    }
  }
  return null
}
