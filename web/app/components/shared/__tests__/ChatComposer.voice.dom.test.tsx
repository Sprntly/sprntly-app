// @vitest-environment jsdom
//
// ChatComposer — dictation as a shared-composer default.
//
// `useSpeechInput` moved INTO this file (2026-08): a caller that omits
// `voiceSupported`/`voiceListening`/`onToggleVoice` gets a self-contained mic
// wired to the composer's OWN recognizer instance instead of having to
// remember to thread one — the fix for a drift class (two of three composer
// consumers previously hard-disabled the mic with `voiceSupported={false}`).
// `ChatScreen` keeps its own richer wiring (draft-join base, cancel-on-send,
// error hint) by continuing to pass all three explicitly, which fully
// overrides the internal wiring — that path is unaffected and is covered by
// `ChatScreen.voice.dom.test.tsx` / `ChatScreen.composer.dom.test.tsx`.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// ── The fake speech engine (same shape ChatScreen.voice.dom.test.tsx uses) ──
class FakeRecognition {
  static instances: FakeRecognition[] = []
  lang = ""
  continuous = false
  interimResults = false
  maxAlternatives = 0
  running = false
  onresult: ((e: { results: unknown }) => void) | null = null
  onerror: ((e: { error: string }) => void) | null = null
  onend: (() => void) | null = null

  constructor() {
    FakeRecognition.instances.push(this)
  }
  start() {
    if (this.running) throw new Error("InvalidStateError")
    this.running = true
  }
  stop() {
    this.running = false
  }
  abort() {
    this.running = false
  }
  emit(phrases: string[], isFinal = true) {
    const results = phrases.map((t) => ({ 0: { transcript: t }, isFinal, length: 1 }))
    this.onresult?.({ results: Object.assign(results, { length: results.length }) })
  }
}
const rec = () => FakeRecognition.instances[FakeRecognition.instances.length - 1]

import { ChatComposer } from "../ChatComposer"

/** A minimal controlled-draft harness — every prop `ChatComposer` requires,
 *  nothing composer-specific beyond what dictation touches (`draft`/`onInput`/
 *  `composerRef`). */
function Harness(props: {
  voiceSupported?: boolean
  voiceListening?: boolean
  onToggleVoice?: () => void
  disableVoice?: boolean
}) {
  const [draft, setDraft] = React.useState("")
  const composerRef = React.useRef<HTMLTextAreaElement>(null)
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  return React.createElement(ChatComposer, {
    busy: false,
    draft,
    pinnedSkill: null,
    attachments: [],
    hint: null,
    menuOpen: false,
    menuActiveIndex: 0,
    slashMenu: null,
    composerRef,
    fileInputRef,
    onInput: (e: React.ChangeEvent<HTMLTextAreaElement>) => setDraft(e.target.value),
    onKeyDown: () => {},
    onSend: () => {},
    onStop: () => {},
    onToggleMenu: () => {},
    onMenuActive: () => {},
    onMenuSelect: () => {},
    onCloseMenu: () => {},
    onRemoveAttachment: () => {},
    onRemoveSkill: () => {},
    onFileSelect: () => {},
    ...props,
  })
}

const textarea = () => document.querySelector(".cx-input") as HTMLTextAreaElement
const micButton = () => screen.queryByLabelText("Dictate your question") as HTMLButtonElement | null
const stopMicButton = () => screen.queryByLabelText("Stop dictating") as HTMLButtonElement | null

beforeEach(() => {
  FakeRecognition.instances = []
  ;(window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRecognition
})
afterEach(() => {
  cleanup()
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition
})

describe("ChatComposer — dictation default-on", () => {
  it("renders the mic and wires useSpeechInput internally when no voice props are passed", async () => {
    render(React.createElement(Harness, {}))
    await act(async () => {})
    expect(micButton()).toBeTruthy()

    await act(async () => {
      fireEvent.click(micButton()!)
    })
    await act(async () => {
      rec().emit(["what did the team decide"])
    })
    // The transcript landed in the SAME controlled `draft` the caller owns —
    // proof the internal hook drives the composer's own `onInput`, not a
    // second, disconnected text source.
    expect(textarea().value).toBe("what did the team decide")
    expect(stopMicButton()).toBeTruthy()
  })

  it("disableVoice renders NO microphone, even though the browser supports dictation", () => {
    render(React.createElement(Harness, { disableVoice: true }))
    expect(micButton()).toBeNull()
    expect(document.querySelector(".cx-mic")).toBeNull()
  })

  it("mic renders across all three composer configs: explicit override (ChatScreen), and default-on (project chats)", () => {
    // ChatScreen's own shape: all three voice props passed explicitly.
    const { unmount: unmountOverride } = render(
      React.createElement(Harness, { voiceSupported: true, voiceListening: false, onToggleVoice: () => {} }),
    )
    expect(micButton()).toBeTruthy()
    unmountOverride()
    cleanup()

    // Project individual/group chat's shape post-fix: no voice props at all
    // (was `voiceSupported={false}` before this ticket) — default-on kicks
    // in and offers the mic exactly like the override path does.
    render(React.createElement(Harness, {}))
    expect(micButton()).toBeTruthy()
  })

  it("an override's voiceSupported={false} still renders no mic — the override fully replaces the default", () => {
    render(React.createElement(Harness, { voiceSupported: false, voiceListening: false, onToggleVoice: () => {} }))
    expect(micButton()).toBeNull()
  })
})
