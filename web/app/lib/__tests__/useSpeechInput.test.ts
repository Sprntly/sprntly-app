// @vitest-environment jsdom
//
// useSpeechInput — the Web Speech API wrapper behind the chat composer's
// microphone.
//
// jsdom implements no speech API at all, which is the point: every test here
// installs a FAKE recognizer on `window` and drives it by firing the same events
// Chrome fires. That makes the two things worth pinning testable without a
// browser or a microphone:
//
//   • the cumulative transcript survives the engine's silent restarts. Chrome
//     closes a session after a few seconds of quiet even with `continuous` set,
//     and each new session's `results` list starts EMPTY — so a naive
//     rebuild-from-index-0 loses every word spoken before the pause.
//   • a pause is not an error. `no-speech` and `aborted` must never reach the
//     user; `not-allowed` must, because it is the one they can act on.
import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useSpeechInput } from "../useSpeechInput"

// ── A fake recognizer, driven by hand ────────────────────────────────────────
// Mirrors the members useSpeechInput touches, and records start/stop/abort so
// the restart behaviour is observable.
type Handlers = {
  onresult: ((e: { results: unknown }) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
}

class FakeRecognition implements Handlers {
  static instances: FakeRecognition[] = []
  lang = ""
  continuous = false
  interimResults = false
  maxAlternatives = 0
  starts = 0
  stops = 0
  aborts = 0
  running = false
  onresult: Handlers["onresult"] = null
  onerror: Handlers["onerror"] = null
  onend: Handlers["onend"] = null

  constructor() {
    FakeRecognition.instances.push(this)
  }
  start() {
    if (this.running) throw new Error("InvalidStateError")
    this.running = true
    this.starts += 1
  }
  stop() {
    this.running = false
    this.stops += 1
  }
  abort() {
    this.running = false
    this.aborts += 1
  }

  /** Deliver a result event the way the engine does: the WHOLE session's list,
   *  every time, with interim entries replaced in place as they firm up. */
  emit(phrases: string[]) {
    const results = phrases.map((t) => ({ 0: { transcript: t }, isFinal: true, length: 1 }))
    this.onresult?.({ results: Object.assign(results, { length: results.length }) })
  }
  /** The engine closing the session on its own (silence), or acknowledging a
   *  stop() — indistinguishable from the outside, which is why the hook tracks
   *  intent separately. */
  end() {
    this.running = false
    this.onend?.()
  }
  fail(error: string) {
    this.onerror?.({ error })
  }
}

const latest = () => FakeRecognition.instances[FakeRecognition.instances.length - 1]

function installApi(name: "SpeechRecognition" | "webkitSpeechRecognition" = "webkitSpeechRecognition") {
  ;(window as unknown as Record<string, unknown>)[name] = FakeRecognition
}

beforeEach(() => {
  FakeRecognition.instances = []
})
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).SpeechRecognition
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition
  vi.restoreAllMocks()
})

describe("feature detection", () => {
  // Firefox. The caller renders no microphone on this, so it has to be right.
  it("reports unsupported when the browser has neither constructor", () => {
    const { result } = renderHook(() => useSpeechInput(() => {}))
    expect(result.current.supported).toBe(false)
  })

  // The prefixed name is the one that actually exists in Chrome and Safari.
  it("reports supported from the webkit-prefixed constructor", () => {
    installApi("webkitSpeechRecognition")
    const { result } = renderHook(() => useSpeechInput(() => {}))
    expect(result.current.supported).toBe(true)
  })

  it("reports supported from the unprefixed constructor", () => {
    installApi("SpeechRecognition")
    const { result } = renderHook(() => useSpeechInput(() => {}))
    expect(result.current.supported).toBe(true)
  })

  // start() on an unsupported browser must be inert, not a crash — the caller
  // hides the button, but nothing else guards the call.
  it("start() is a no-op with no API present", () => {
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())
    expect(result.current.listening).toBe(false)
    expect(onTranscript).not.toHaveBeenCalled()
  })
})

describe("dictating", () => {
  it("configures the recognizer for a multi-sentence question", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    // `continuous` off ends the session at the first breath; `interimResults`
    // off leaves the box empty for seconds and reads as broken.
    expect(latest().continuous).toBe(true)
    expect(latest().interimResults).toBe(true)
    expect(result.current.listening).toBe(true)
  })

  it("emits the transcript as it firms up, cumulatively", () => {
    installApi()
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())

    act(() => latest().emit(["why did"]))
    expect(onTranscript).toHaveBeenLastCalledWith("why did")
    // The engine re-sends the whole list with the phrase extended — the hook
    // must ASSIGN this, never append it, or the draft reads "why didwhy did
    // churn rise".
    act(() => latest().emit(["why did churn rise"]))
    expect(onTranscript).toHaveBeenLastCalledWith("why did churn rise")
  })

  // The regression this hook exists to avoid. Chrome ends the session after a
  // few seconds of silence; the next session's result list starts empty.
  it("keeps everything said before a silent restart", () => {
    installApi()
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())

    act(() => latest().emit(["why did churn rise"]))
    // …the user pauses to think, and the engine closes the session…
    act(() => latest().end())
    // …the hook restarts it, silently, on the SAME recognizer.
    expect(result.current.listening).toBe(true)
    expect(latest().starts).toBe(2)

    // The new session knows nothing of the first, so its list starts fresh.
    act(() => latest().emit(["in the enterprise tier"]))
    expect(onTranscript).toHaveBeenLastCalledWith("why did churn rise in the enterprise tier")
  })

  it("does not restart after the user presses stop", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => result.current.stop())
    expect(latest().stops).toBe(1)
    // stop(), not abort() — a phrase mid-finalisation still arrives.
    expect(latest().aborts).toBe(0)
    expect(result.current.listening).toBe(false)

    // The engine's own `end` follows the stop; it must not resurrect the mic.
    act(() => latest().end())
    expect(result.current.listening).toBe(false)
    expect(latest().starts).toBe(1)
  })

  // stop() vs cancel() differ on exactly one thing: what happens to the phrase
  // the engine was still finalising. The mic button keeps it (it is the last
  // thing the user said); a send throws it away (the draft it would land in is
  // gone). Getting this backwards refills the composer with the question that
  // was just sent.
  it("cancel() unwires results, so a trailing phrase lands nowhere", () => {
    installApi()
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())
    act(() => latest().emit(["why did churn rise"]))
    onTranscript.mockClear()

    act(() => result.current.cancel())
    expect(result.current.listening).toBe(false)
    expect(latest().aborts).toBe(1)

    act(() => latest().emit(["why did churn rise"]))
    expect(onTranscript).not.toHaveBeenCalled()
  })

  it("stop() keeps delivering the phrase still being finalised", () => {
    installApi()
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())
    act(() => latest().emit(["why did churn"]))
    act(() => result.current.stop())

    // The engine's last word arrives after the button was pressed, and counts.
    act(() => latest().emit(["why did churn rise"]))
    expect(onTranscript).toHaveBeenLastCalledWith("why did churn rise")
  })

  it("starts a fresh transcript on the next session", () => {
    installApi()
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())
    act(() => latest().emit(["first question"]))
    act(() => result.current.stop())
    act(() => latest().end())

    act(() => result.current.start())
    act(() => latest().emit(["second question"]))
    // Not "first question second question" — the previous dictation is done.
    expect(onTranscript).toHaveBeenLastCalledWith("second question")
  })

  it("ignores a second start() while already listening", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => result.current.start())
    expect(FakeRecognition.instances).toHaveLength(1)
    expect(latest().starts).toBe(1)
  })
})

describe("errors", () => {
  // A pause is not a failure. Surfacing `no-speech` would mean the microphone
  // accused you of something every time you drew breath.
  it("stays silent and stays listening through no-speech", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => latest().fail("no-speech"))
    expect(result.current.error).toBeNull()
    expect(result.current.listening).toBe(true)
  })

  // `aborted` is our own doing.
  it("stays silent through aborted", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => latest().fail("aborted"))
    expect(result.current.error).toBeNull()
  })

  // The one the user can actually fix, so it says where to fix it.
  it("surfaces a blocked microphone and turns the mic off", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => latest().fail("not-allowed"))
    expect(result.current.error).toMatch(/blocked/i)
    expect(result.current.error).toMatch(/browser settings/i)
    expect(result.current.listening).toBe(false)
  })

  it("does not restart after a fatal error", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => latest().fail("audio-capture"))
    act(() => latest().end())
    expect(latest().starts).toBe(1)
    expect(result.current.listening).toBe(false)
  })

  it("clears a previous error when dictation is started again", () => {
    installApi()
    const { result } = renderHook(() => useSpeechInput(() => {}))
    act(() => result.current.start())
    act(() => latest().fail("network"))
    expect(result.current.error).toBeTruthy()
    act(() => result.current.start())
    expect(result.current.error).toBeNull()
  })
})

describe("teardown", () => {
  // The composer is gone; a late transcript has nowhere to land, and a handler
  // still wired to it would set state on a dead component.
  it("aborts and unwires the recognizer on unmount", () => {
    installApi()
    const onTranscript = vi.fn()
    const { result, unmount } = renderHook(() => useSpeechInput(onTranscript))
    act(() => result.current.start())
    const rec = latest()
    unmount()
    expect(rec.aborts).toBe(1)
    expect(rec.onresult).toBeNull()
    expect(rec.onerror).toBeNull()
    expect(rec.onend).toBeNull()
  })
})
