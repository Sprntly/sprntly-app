/**
 * Google Drive file picker — mounted in the Configure drawer's slot for the
 * Google Drive connector. Replaces the old folder browser (drive.readonly):
 * under the drive.file scope this app can only see files the user explicitly
 * picks via Google's own Picker widget, so we lazily load the Picker JS, mint
 * a short-lived access token from the backend, and POST the picked file ids
 * back to be synced into the corpus.
 *
 * Pure View pattern (props in, JSX out) for unit testing via
 * renderToStaticMarkup, plus a hooks-wired wrapper that handles the Picker
 * round-trip. The Picker JS is an external browser global, so the View is kept
 * free of it — only the wrapper touches `window`.
 */
"use client"

import { useCallback, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type GoogleDrivePickedFile,
} from "../../lib/api"

// ─────────────── Minimal typings for the Google Picker globals ───────────────
// We load the Picker via Google's CDN script (no npm package), so declare just
// the slice of the API we touch. Kept tight on purpose.
type GapiLoad = (name: string, cb: () => void) => void
type PickerDoc = { id: string; name?: string }
type PickerResponse = { action: string; docs?: PickerDoc[] }

interface GooglePicker {
  PickerBuilder: new () => {
    addView: (view: unknown) => GooglePicker["PickerBuilder"]["prototype"]
    setOAuthToken: (t: string) => GooglePicker["PickerBuilder"]["prototype"]
    setDeveloperKey: (k: string) => GooglePicker["PickerBuilder"]["prototype"]
    enableFeature: (f: unknown) => GooglePicker["PickerBuilder"]["prototype"]
    setCallback: (
      cb: (data: PickerResponse) => void,
    ) => GooglePicker["PickerBuilder"]["prototype"]
    build: () => { setVisible: (v: boolean) => void }
  }
  DocsView: new (viewId?: unknown) => { setMode: (m: unknown) => unknown }
  ViewId: { DOCS: unknown }
  DocsViewMode: { LIST: unknown }
  Feature: { MULTISELECT_ENABLED: unknown }
  Action: { PICKED: string }
  Response: { ACTION: string; DOCUMENTS: string }
}

declare global {
  interface Window {
    gapi?: { load: GapiLoad }
    google?: { picker?: GooglePicker }
  }
}

const PICKER_SCRIPT_SRC = "https://apis.google.com/js/api.js"

/** Load the Google API JS once, then load the `picker` module. Browser-only,
 * idempotent: a second call reuses the in-flight / resolved promise so the
 * script is never injected twice. */
let pickerLoadPromise: Promise<void> | null = null
function loadPicker(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Picker can only load in the browser"))
  }
  if (window.google?.picker) return Promise.resolve()
  if (pickerLoadPromise) return pickerLoadPromise

  pickerLoadPromise = new Promise<void>((resolve, reject) => {
    const onApiReady = () => {
      if (!window.gapi) {
        reject(new Error("Google API failed to load"))
        return
      }
      window.gapi.load("picker", () => resolve())
    }

    // Reuse an existing tag if one is already on the page (guards double-load).
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${PICKER_SCRIPT_SRC}"]`,
    )
    if (existing) {
      if (window.gapi) onApiReady()
      else existing.addEventListener("load", onApiReady, { once: true })
      existing.addEventListener(
        "error",
        () => reject(new Error("Failed to load the Google Picker script")),
        { once: true },
      )
      return
    }

    const script = document.createElement("script")
    script.src = PICKER_SCRIPT_SRC
    script.async = true
    script.onload = onApiReady
    script.onerror = () =>
      reject(new Error("Failed to load the Google Picker script"))
    document.head.appendChild(script)
  })
  // Reset on failure so a later click can retry.
  pickerLoadPromise.catch(() => {
    pickerLoadPromise = null
  })
  return pickerLoadPromise
}

// ─────────────────────────── Pure View ───────────────────────────

export type GoogleDrivePickerViewProps = {
  savedFiles: GoogleDrivePickedFile[]
  /** True when the API key env is missing — the Picker can't be configured. */
  configured: boolean
  /** Token fetch / save in flight. */
  busy: boolean
  /** Inline error from token fetch or save, or null. */
  error: string | null
  onAddFiles: () => void
}

export function GoogleDrivePickerView({
  savedFiles,
  configured,
  busy,
  error,
  onAddFiles,
}: GoogleDrivePickerViewProps) {
  if (!configured) {
    return (
      <div className="conn-drive-setup">
        <p className="conn-drive-error" role="alert">
          Drive file picking isn&apos;t configured. Ask your admin to set the
          Google API key.
        </p>
      </div>
    )
  }

  return (
    <div className="conn-drive-setup">
      {savedFiles.length > 0 ? (
        <div className="conn-drive-saved">
          <span className="conn-drive-selected-label">Synced files</span>
          <ul className="conn-drive-file-list">
            {savedFiles.map((f) => (
              <li key={f.id} className="conn-drive-file">
                {f.name ?? f.id}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="conn-drive-empty">
          No Drive files selected yet. Pick the files you want Sprntly to read.
        </p>
      )}

      {error ? (
        <p className="conn-drive-error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="conn-drive-browser-actions">
        <button
          type="button"
          className="btn btn-sm btn-primary"
          disabled={busy}
          onClick={onAddFiles}
        >
          {busy ? "Opening…" : "Add Drive files"}
        </button>
      </div>
    </div>
  )
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  dataset: string
  savedFiles?: GoogleDrivePickedFile[]
  /** Fired after a successful save so the parent can reload connections. */
  onSaved?: () => void
}

export function GoogleDrivePicker({ dataset: _dataset, savedFiles, onSaved }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_API_KEY
  const configured = Boolean(apiKey)

  const handleAddFiles = useCallback(async () => {
    if (!apiKey) return
    setBusy(true)
    setError(null)
    try {
      // Lazy, browser-only, double-load-guarded Picker JS load.
      await loadPicker()
      const token = await connectorsApi.getGoogleDrivePickerToken()
      const picker = window.google?.picker
      if (!picker) throw new Error("Google Picker failed to initialize")

      const view = new picker.DocsView(picker.ViewId.DOCS).setMode(
        picker.DocsViewMode.LIST,
      )

      const built = new picker.PickerBuilder()
        .setDeveloperKey(apiKey)
        .setOAuthToken(token.access_token)
        .addView(view)
        .enableFeature(picker.Feature.MULTISELECT_ENABLED)
        .setCallback((data: PickerResponse) => {
          if (data.action !== picker.Action.PICKED) return
          const files: GoogleDrivePickedFile[] = (data.docs ?? []).map((d) => ({
            id: d.id,
            name: d.name,
          }))
          if (files.length === 0) return
          // Persist + sync. Surface any failure inline.
          void (async () => {
            setBusy(true)
            setError(null)
            try {
              await connectorsApi.saveGoogleDriveFiles({ files })
              onSaved?.()
            } catch (e) {
              setError(toMessage(e))
            } finally {
              setBusy(false)
            }
          })()
        })
        .build()

      built.setVisible(true)
    } catch (e) {
      setError(toMessage(e))
    } finally {
      setBusy(false)
    }
  }, [apiKey, onSaved])

  return (
    <GoogleDrivePickerView
      savedFiles={savedFiles ?? []}
      configured={configured}
      busy={busy}
      error={error}
      onAddFiles={() => void handleAddFiles()}
    />
  )
}

function toMessage(e: unknown): string {
  if (e instanceof ApiError) return apiErrorMessage(e.status, e.body)
  if (e instanceof Error) return e.message
  return String(e)
}
