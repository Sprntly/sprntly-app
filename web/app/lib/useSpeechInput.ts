"use client"

import { useCallback, useEffect, useRef, useState } from "react"

/**
 * Dictation for a composer, via the browser's own Web Speech API.
 *
 * No backend, no key, no per-minute cost: the browser owns the recognizer. The
 * trade that buys is where the audio goes — Chrome's implementation is
 * SERVER-BASED and ships the microphone stream to Google's speech service, so a
 * question dictated here is spoken to a third party we do not otherwise send
 * data to. That was the explicit product call over a server-side Whisper route
 * (`app/kg_ingest/transcription.py`, already built for meeting audio), taken for
 * zero cost and live word-by-word feedback. If that trade is ever revisited, the
 * swap is this hook's internals — the composer only knows `start`/`stop`/text.
 *
 * Support, verified against MDN + caniuse (Aug 2026): Chrome/Edge/Opera and
 * Safari 14.1+ ship it, universally behind `webkitSpeechRecognition`; Firefox
 * keeps it behind a flag. `supported` is therefore load-bearing — the caller
 * renders NO microphone at all where the API is missing, rather than a button
 * that does nothing on a third of desktops.
 *
 * The API is not in TypeScript's DOM lib, so the shapes below are declared
 * structurally rather than imported. They are deliberately minimal — only the
 * members this hook touches.
 */

type SpeechAlternative = { transcript: string }
type SpeechResult = { readonly length: number; 0: SpeechAlternative; isFinal: boolean }
type SpeechResultList = { readonly length: number; [index: number]: SpeechResult }
type SpeechResultEvent = { results: SpeechResultList }
type SpeechErrorEvent = { error: string }

type Recognition = {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((e: SpeechResultEvent) => void) | null
  onerror: ((e: SpeechErrorEvent) => void) | null
  onend: (() => void) | null
}

type RecognitionCtor = new () => Recognition

/** The vendor-prefixed name is still the only one Chrome and Safari both
 *  answer to, so the unprefixed constructor is tried first and the webkit one
 *  is the fallback that actually fires today. */
function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/**
 * Errors worth interrupting someone for, mapped to what they can DO about it.
 *
 * `no-speech` and `aborted` are deliberately absent: the first is a pause in a
 * sentence and the second is our own `stop()`. Surfacing either as an error
 * would mean the mic accused you of failing every time you drew breath.
 */
const FATAL_ERRORS: Record<string, string> = {
  "not-allowed":
    "Microphone access is blocked. Allow it for this site in your browser settings, then try again.",
  "service-not-allowed":
    "Your browser wouldn't start its speech service. Check this site's microphone permission.",
  "audio-capture": "No microphone found. Connect one and try again.",
  network: "Dictation needs a connection — the browser's speech service couldn't be reached.",
  "language-not-supported": "Your browser doesn't offer dictation in this language.",
}

/** Chrome ends a recognition session on its own after a few seconds of quiet,
 *  even with `continuous` set, so staying on across a pause means restarting.
 *  This bounds that loop for a microphone that yields nothing at all: the count
 *  resets on every result, so real dictation never approaches it, and at
 *  Chrome's ~5s silent session it is a few minutes of dead air before the mic
 *  gives up on its own. */
const MAX_SILENT_RESTARTS = 40

/** Join two transcript fragments without gluing the last word of one to the
 *  first of the next. */
function join(a: string, b: string): string {
  if (!a) return b
  if (!b) return a
  return `${a} ${b}`
}

export type SpeechInput = {
  /** The browser has the API at all. False → render no microphone. */
  supported: boolean
  /** The mic is on (the user's intent, not the engine's momentary state — it
   *  stays true across the silent restarts above). */
  listening: boolean
  /** A fatal, actionable problem. Cleared on the next `start()`. */
  error: string | null
  start: () => void
  /** "I've finished speaking" — the graceful end. A phrase still being
   *  finalised when this lands is the last thing the user said, and it still
   *  arrives. This is the microphone BUTTON's exit. */
  stop: () => void
  /** "Throw away what's in flight" — the abrupt end, for when the draft the
   *  transcript would land in no longer exists (the question was sent). Unwires
   *  the result handler BEFORE aborting, so the tail of the sent question cannot
   *  reappear in the composer that was just cleared. */
  cancel: () => void
}

/**
 * @param onTranscript Called with the FULL text spoken since `start()` — finals
 *   and the in-flight interim phrase together, so the caller can render live
 *   words and let them firm up in place. It is cumulative, never a delta: the
 *   caller assigns it rather than appending, which makes a repeated or replayed
 *   event harmless.
 */
export function useSpeechInput(onTranscript: (text: string) => void): SpeechInput {
  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const recRef = useRef<Recognition | null>(null)
  // The user's intent, kept in a ref because the engine's own callbacks read it
  // outside React's render cycle — `onend` has to know whether a stop was asked
  // for or just happened.
  const wantRef = useRef(false)
  const restartsRef = useRef(0)
  // Text from sub-sessions the engine already closed. A restart resets the
  // engine's own `results` list to empty, so without this every silent pause
  // would erase the sentence before it.
  const committedRef = useRef("")
  const sessionRef = useRef("")

  const cbRef = useRef(onTranscript)
  useEffect(() => {
    cbRef.current = onTranscript
  })

  // Detected after mount, never during render: this is a static export, so the
  // first pass runs in Node during `next build` where `window` doesn't exist,
  // and a value that differed between that pass and the browser's would be a
  // hydration mismatch.
  useEffect(() => {
    setSupported(!!recognitionCtor())
  }, [])

  const buildRecognizer = useCallback((): Recognition | null => {
    const Ctor = recognitionCtor()
    if (!Ctor) return null
    const rec = new Ctor()
    rec.lang = (typeof navigator !== "undefined" && navigator.language) || "en-US"
    // A dictated question is a paragraph, not a phrase. With `continuous` off,
    // the engine closes at the first breath and the rest of the sentence is
    // simply not heard.
    rec.continuous = true
    // The words appear as they are spoken and firm up behind you. Without this
    // the box stays empty for seconds at a time and reads as broken.
    rec.interimResults = true
    rec.maxAlternatives = 1

    rec.onresult = (e) => {
      // The engine hands back the whole session's result list on every event,
      // so the transcript is rebuilt from index 0 rather than tracked as a
      // delta. One source of truth, and a re-delivered event can't duplicate a
      // phrase into the draft.
      let text = ""
      for (let i = 0; i < e.results.length; i++) {
        text += e.results[i][0].transcript
      }
      sessionRef.current = text.trim()
      restartsRef.current = 0
      cbRef.current(join(committedRef.current, sessionRef.current))
    }

    rec.onerror = (e) => {
      const message = FATAL_ERRORS[e.error]
      if (!message) return // a pause, or our own stop()
      wantRef.current = false
      setError(message)
      setListening(false)
    }

    rec.onend = () => {
      // Fold the closed session into the committed text BEFORE any restart —
      // the new session starts from an empty result list.
      committedRef.current = join(committedRef.current, sessionRef.current)
      sessionRef.current = ""
      if (wantRef.current && restartsRef.current < MAX_SILENT_RESTARTS) {
        restartsRef.current += 1
        try {
          rec.start()
          return
        } catch {
          // Already running, or the engine refused to resume — fall through and
          // settle as stopped rather than leaving a mic that looks live.
        }
      }
      wantRef.current = false
      setListening(false)
    }

    return rec
  }, [])

  const start = useCallback(() => {
    if (wantRef.current) return
    const rec = buildRecognizer()
    if (!rec) {
      setSupported(false)
      return
    }
    setError(null)
    committedRef.current = ""
    sessionRef.current = ""
    restartsRef.current = 0
    recRef.current = rec
    wantRef.current = true
    try {
      rec.start()
      setListening(true)
    } catch {
      // start() throws synchronously only if a session is already open; a denied
      // permission arrives as an `error` EVENT, not a throw, so it is handled
      // above and not here.
      wantRef.current = false
      setListening(false)
      setError("Dictation couldn't start. Reload the page and try again.")
    }
  }, [buildRecognizer])

  const stop = useCallback(() => {
    wantRef.current = false
    setListening(false)
    // stop(), not abort() — a phrase still being finalised when the button is
    // pressed is the last thing the user said, and it still arrives.
    try {
      recRef.current?.stop()
    } catch {
      // Not running. Nothing to stop.
    }
  }, [])

  const cancel = useCallback(() => {
    wantRef.current = false
    setListening(false)
    committedRef.current = ""
    sessionRef.current = ""
    const rec = recRef.current
    if (!rec) return
    // Unwire FIRST. `stop()` is specified to still deliver the pending final
    // result, and Chrome does — so a send that merely stopped the mic would
    // watch the question it had just cleared write itself back into the box.
    rec.onresult = null
    try {
      rec.abort()
    } catch {
      // Not running.
    }
  }, [])

  // On unmount abort rather than stop: the composer is gone, so a late
  // transcript has nowhere to land, and the handlers are cleared first so a
  // trailing event can't set state on a dead component.
  useEffect(
    () => () => {
      wantRef.current = false
      const rec = recRef.current
      if (!rec) return
      rec.onresult = null
      rec.onerror = null
      rec.onend = null
      try {
        rec.abort()
      } catch {
        // Already finished.
      }
    },
    [],
  )

  return { supported, listening, error, start, stop, cancel }
}
