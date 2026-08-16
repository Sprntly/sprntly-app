import { describe, expect, it } from "vitest"
import { slackShareQuestionFor } from "../slackShareQuestion"
import type { SlackSharePreview, SlackShareTarget } from "../../api"

const TARGET: SlackShareTarget = {
  type: "prd",
  id: 42,
  title: "Checkout Abandonment",
  kind_label: "PRD",
  url: "https://app.sprntly.test/brief?prd=42",
}

const CHANNELS = [
  { id: "C1", name: "product", is_private: false, is_member: true },
  { id: "C2", name: "product-leads", is_private: false, is_member: false },
  { id: "C3", name: "founders", is_private: true, is_member: false },
]

function preview(over: Partial<SlackSharePreview> = {}): SlackSharePreview {
  return { status: "ready", target: TARGET, channel: CHANNELS[0], channels: [], ...over }
}

describe("slackShareQuestionFor — which channel", () => {
  const needsChannel = (over: Partial<SlackSharePreview> = {}) =>
    preview({ status: "needs_channel", channel: null, channels: CHANNELS, ...over })

  it("asks with every visible channel as an option", () => {
    const q = slackShareQuestionFor(needsChannel())
    expect(q?.kind).toBe("channel")
    expect(q?.options.map((o) => o.label)).toEqual(["#product", "#product-leads", "#founders"])
  })

  it("answers with the channel NAME, not its id", () => {
    // The host re-previews by name so the server re-runs its own membership
    // and private-channel checks against the channel actually picked.
    expect(slackShareQuestionFor(needsChannel())?.options[0].value).toBe("product")
  })

  it("says on the option whether Sprntly will have to join", () => {
    const q = slackShareQuestionFor(needsChannel())
    expect(q?.options[0].description).toBeNull()               // already a member
    expect(q?.options[1].description).toContain("will join")   // public, not in
    expect(q?.options[2].description).toContain("can't add itself")  // private
  })

  it("names the channel that failed to match in the prompt", () => {
    const q = slackShareQuestionFor(needsChannel({ channel_query: "prodcut" }))
    expect(q?.prompt).toContain("#prodcut")
  })

  it("opens no popup when there is nothing to choose between", () => {
    // The card says "Sprntly can't see any Slack channels" itself; an empty
    // stepper would be a question with no answers.
    expect(slackShareQuestionFor(needsChannel({ channels: [] }))).toBeNull()
  })
})

describe("slackShareQuestionFor — which document", () => {
  const other: SlackShareTarget = { ...TARGET, id: 43, title: "Checkout Redesign" }

  it("asks between the candidates, keyed by type and id", () => {
    const q = slackShareQuestionFor(preview({
      status: "ambiguous_target", target: null, candidates: [TARGET, other],
    }))
    expect(q?.kind).toBe("target")
    expect(q?.options.map((o) => o.value)).toEqual(["prd-42", "prd-43"])
    expect(q?.options[0].label).toBe("Checkout Abandonment")
    expect(q?.options[0].description).toBe("PRD")
  })

  it("opens no popup with no candidates", () => {
    expect(slackShareQuestionFor(preview({
      status: "ambiguous_target", target: null, candidates: [],
    }))).toBeNull()
  })
})

describe("slackShareQuestionFor — nothing to ask", () => {
  it.each([
    ["ready — the card shows Send", preview()],
    ["blocked — the answer is to invite the bot, not to pick again",
      preview({ status: "blocked" })],
    ["unsupported_type — the kind can't be shared at all",
      preview({ status: "unsupported_type", target: null })],
    ["needs_target — nothing matched, so there is nothing to choose",
      preview({ status: "needs_target", target: null, candidates: [] })],
  ])("%s", (_label, p) => {
    expect(slackShareQuestionFor(p)).toBeNull()
  })
})
