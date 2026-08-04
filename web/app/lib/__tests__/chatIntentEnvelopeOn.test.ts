// The one gate behind action-envelope chat dispatch. It is DEFAULT ON: the
// staff checkbox is a per-company kill switch, not an opt-in.
//
// This is the whole three-state contract in one place, and it is the ONLY
// coverage that reaches the BriefChat read site — that surface's composer was
// removed on 2026-07-19 (7120b4f0), so its submitAsk (and the gate inside it)
// is unreachable from the DOM. ChatScreen's wiring is covered end-to-end in
// ChatScreen.envelope-default.dom.test.tsx; the staff panel's in
// StaffAdminScreen.dom.test.tsx.
import { describe, expect, it } from "vitest"

import {
  DEFAULT_FEATURE_FLAGS,
  chatIntentEnvelopeOn,
  parseFeatureFlags,
} from "../onboarding/types"

describe("chatIntentEnvelopeOn", () => {
  it("explicit true → on", () => {
    expect(chatIntentEnvelopeOn({ chat_intent_envelope: true })).toBe(true)
  })

  it("KEY ABSENT → on (grandfathered, no data migration needed)", () => {
    expect(chatIntentEnvelopeOn({})).toBe(true)
    expect(chatIntentEnvelopeOn({ agents: true, top_insights: false })).toBe(true)
  })

  it("explicit false → off, the only way off", () => {
    expect(chatIntentEnvelopeOn({ chat_intent_envelope: false })).toBe(false)
  })

  it("UNKNOWN flags (null/undefined) → on: it fails OPEN", () => {
    // A workspace that hasn't loaded, or whose read failed. Deliberately the
    // OPPOSITE of ds_claude_analysis, which fails closed: that flag decides
    // whether a tenant's raw data leaves the box, this one only picks which
    // router reads the message. The envelope request keeps its own fallback to
    // the legacy ladder if it errors, so failing open costs a round-trip at
    // worst — never a wrong answer.
    expect(chatIntentEnvelopeOn(null)).toBe(true)
    expect(chatIntentEnvelopeOn(undefined)).toBe(true)
  })

  it("only the literal `false` is off — no other falsy value counts", () => {
    // Guards the `!== false` shape: a half-written row must not silently kill
    // a company's routing.
    const odd = { chat_intent_envelope: undefined } as unknown as Record<string, boolean>
    expect(chatIntentEnvelopeOn(odd)).toBe(true)
    expect(chatIntentEnvelopeOn({ chat_intent_envelope: null as unknown as boolean })).toBe(true)
  })

  it("agrees with DEFAULT_FEATURE_FLAGS and survives parseFeatureFlags", () => {
    // The parsed and unparsed shapes must resolve the same way — the gate no
    // longer depends on parseFeatureFlags having run first.
    expect(DEFAULT_FEATURE_FLAGS.chat_intent_envelope).toBe(true)
    expect(chatIntentEnvelopeOn(DEFAULT_FEATURE_FLAGS)).toBe(true)
    expect(chatIntentEnvelopeOn(parseFeatureFlags({}))).toBe(true)
    expect(chatIntentEnvelopeOn(parseFeatureFlags(null))).toBe(true)
    expect(chatIntentEnvelopeOn(parseFeatureFlags({ agents: false }))).toBe(true)
    // An explicit false survives parsing intact.
    expect(
      chatIntentEnvelopeOn(parseFeatureFlags({ chat_intent_envelope: false })),
    ).toBe(false)
  })
})
