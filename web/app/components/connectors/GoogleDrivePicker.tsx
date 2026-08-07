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

import { useCallback, useEffect, useRef, useState } from "react"
import {
  ApiError,
  apiErrorMessage,
  connectorsApi,
  type GoogleDrivePickedFile,
  type GoogleDriveTreeNode,
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
    setAppId: (id: string) => GooglePicker["PickerBuilder"]["prototype"]
    build: () => { setVisible: (v: boolean) => void }
  }
  // Chainable in the real API; typed as a self-returning builder so folder
  // options can be applied in sequence.
  DocsView: new (viewId?: unknown) => {
    setMode: (m: unknown) => GooglePicker["DocsView"]["prototype"]
    setIncludeFolders: (v: boolean) => GooglePicker["DocsView"]["prototype"]
    setSelectFolderEnabled: (v: boolean) => GooglePicker["DocsView"]["prototype"]
  }
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

const GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"

function isFolderNode(n: GoogleDriveTreeNode): boolean {
  return (n.mimeType ?? "") === GOOGLE_FOLDER_MIME
}

/** Recursively render the children of `parentId` from a flat node list (the
 * shape `google_drive_sync.expand_folder` stores). Folders become nested
 * disclosures; files become leaf rows. Legacy flat data (nodes with no
 * `parentId`) is treated as a direct child of `rootId`, so it renders as a
 * flat list rather than disappearing. */
function DriveTreeChildren({
  nodes,
  parentId,
  rootId,
}: {
  nodes: GoogleDriveTreeNode[]
  parentId: string
  rootId: string
}) {
  const children = nodes.filter((n) => (n.parentId ?? rootId) === parentId)
  if (children.length === 0) return null
  return (
    <ul className="conn-drive-folder-children">
      {children.map((n) =>
        isFolderNode(n) ? (
          <li key={n.id}>
            <details className="conn-drive-folder conn-drive-subfolder">
              <summary className="conn-drive-file-name">
                <span aria-hidden="true">📁 </span>
                {n.name ?? n.id}
              </summary>
              <DriveTreeChildren nodes={nodes} parentId={n.id} rootId={rootId} />
            </details>
          </li>
        ) : (
          <li key={n.id} className="conn-drive-tree-file">
            <span aria-hidden="true">📄 </span>
            {n.name ?? n.id}
          </li>
        ),
      )}
    </ul>
  )
}

export type GoogleDrivePickerViewProps = {
  savedFiles: GoogleDrivePickedFile[]
  /** True when the API key env is missing — the Picker can't be configured. */
  configured: boolean
  /** Token fetch / save in flight. */
  busy: boolean
  /** Inline error from token fetch or save, or null. */
  error: string | null
  onAddFiles: () => void
  /** Drop one file from the connected set, by Drive file id. */
  onRemoveFile: (id: string) => void
  /** The file id currently being removed, or null. Tracked separately from
   * `busy` so a delete reports itself on the row the user clicked and nowhere
   * else — one shared flag made the Add button announce "Opening…" during a
   * delete, which is a different action on a different control. */
  removingId: string | null
  /** folder id -> the subtree that folder expanded to on the last sync. An
   * entry present here IS a folder; absent means a plain file. Written by the
   * sync, because only Drive knows what is inside a folder and only the sync
   * has looked. */
  folderContents?: Record<string, GoogleDriveTreeNode[]>
}

export function GoogleDrivePickerView({
  savedFiles,
  configured,
  busy,
  error,
  onAddFiles,
  onRemoveFile,
  removingId,
  folderContents,
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
          <span className="conn-drive-selected-label">Selected files</span>
          <ul className="conn-drive-file-list">
            {savedFiles.map((f) => {
              // Only the sync can tell a folder from a file, so presence of a
              // contents entry is the signal. `undefined` = a plain file;
              // an empty array = a folder that expanded to nothing readable,
              // which is a different (and reportable) state.
              const contents = folderContents?.[f.id]
              const isFolder = contents !== undefined
              // Count only FILE nodes for the summary; sub-folders are shown
              // as nested nodes, not counted as files. Legacy flat data (no
              // mimeType) counts every entry as a file, matching prior copy.
              const fileCount = contents
                ? contents.filter((c) => !isFolderNode(c)).length
                : 0
              const subfolderCount = contents
                ? contents.filter((c) => isFolderNode(c)).length
                : 0
              return (
              <li key={f.id} className="conn-drive-file">
                {isFolder ? (
                  <details className="conn-drive-folder">
                    <summary className="conn-drive-file-name">
                      <span aria-hidden="true">📁 </span>
                      {f.name ?? f.id}{" "}
                      <span className="conn-drive-folder-count">
                        {contents.length === 0
                          ? "— empty"
                          : `— ${fileCount} file${fileCount === 1 ? "" : "s"}` +
                            (subfolderCount > 0
                              ? `, ${subfolderCount} folder${subfolderCount === 1 ? "" : "s"}`
                              : "")}
                      </span>
                    </summary>
                    {contents.length > 0 ? (
                      <DriveTreeChildren
                        nodes={contents}
                        parentId={f.id}
                        rootId={f.id}
                      />
                    ) : (
                      <p className="conn-drive-folder-empty">
                        This folder has no readable files inside it.
                      </p>
                    )}
                  </details>
                ) : (
                  <span className="conn-drive-file-name">{f.name ?? f.id}</span>
                )}
                <button
                  type="button"
                  className="conn-drive-file-remove"
                  // Labelled by NAME, not "this file": a screen reader hears
                  // these as a list and needs to tell them apart.
                  aria-label={
                    removingId === f.id
                      ? `Removing ${f.name ?? f.id}`
                      : `Remove ${f.name ?? f.id}`
                  }
                  title={removingId === f.id ? "Removing…" : "Remove"}
                  disabled={busy || removingId !== null}
                  onClick={() => onRemoveFile(f.id)}
                >
                  {removingId === f.id ? "…" : "×"}
                </button>
              </li>
              )
            })}
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
          // Disabled during a remove — both actions POST the whole list, so
          // letting them overlap races one save against the other — but the
          // LABEL never changes for someone else's action.
          disabled={busy || removingId !== null}
          onClick={onAddFiles}
        >
          {busy ? "Opening…" : "Add Drive files"}
        </button>
      </div>
    </div>
  )
}

/**
 * Union of the already-saved files and the newly picked ones, keyed by id.
 *
 * The save endpoint REPLACES the stored list (see
 * `connectorsApi.saveGoogleDriveFiles`) and the Picker hands back only what was
 * chosen in THIS session. Posting that raw meant every trip through "Add Drive
 * files" silently discarded every file added before it — the button said Add
 * and did Replace, and with no remove control nothing put them back.
 *
 * Merged here rather than server-side so the endpoint keeps replace semantics,
 * which is exactly what the remove control needs: "add" is a property of that
 * button, not of the route.
 *
 * Existing order holds and new files append. Re-picking a file already saved
 * refreshes its name — it may have been renamed in Drive since — but never
 * duplicates the row.
 */
export function mergePickedFiles(
  existing: GoogleDrivePickedFile[],
  picked: GoogleDrivePickedFile[],
): GoogleDrivePickedFile[] {
  const byId = new Map<string, GoogleDrivePickedFile>()
  for (const f of existing) byId.set(f.id, f)
  for (const f of picked) {
    const prev = byId.get(f.id)
    byId.set(f.id, { id: f.id, name: f.name ?? prev?.name })
  }
  return [...byId.values()]
}

// ───────────────────── Hooks-wired wrapper ─────────────────────

type Props = {
  dataset: string
  savedFiles?: GoogleDrivePickedFile[]
  /** folder id -> subtree it expanded to, straight off the connection config. */
  folderContents?: Record<string, GoogleDriveTreeNode[]>
  /** Whether the Picker offers folder selection. Gated by the connection's
   * granted OAuth scope (see ConfigureConnectorDrawer): under drive.file a
   * picked folder grants the folder object but nothing beneath it, so
   * offering it is a trap. Defaults to false — the safe, do-nothing-different
   * behaviour when the caller doesn't pass it. Flips on automatically, no
   * other code change needed, once the connection's `scopes` actually
   * contains drive.readonly (a separate, post-CASA change to the scope this
   * app requests). */
  folderSelectEnabled?: boolean
  /** Fired after a successful save so the parent can reload connections. */
  onSaved?: () => void
}

export function GoogleDrivePicker({
  dataset: _dataset,
  savedFiles,
  folderContents,
  folderSelectEnabled = false,
  onSaved,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [removingId, setRemovingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const apiKey = process.env.NEXT_PUBLIC_GOOGLE_API_KEY
  const configured = Boolean(apiKey)

  // The Picker's callback fires long after the builder was constructed — the
  // user is browsing Drive in between — so the saved list is read through a ref
  // rather than whatever the closure captured at build time. A captured value
  // would merge against a stale list and drop files added earlier in the visit.
  const savedFilesRef = useRef<GoogleDrivePickedFile[]>(savedFiles ?? [])
  useEffect(() => {
    savedFilesRef.current = savedFiles ?? []
  }, [savedFiles])

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

      const view = new picker.DocsView(picker.ViewId.DOCS)
        .setMode(picker.DocsViewMode.LIST)
        // Folders are shown so people can BROWSE INTO them and pick the files
        // inside — the natural way to connect "everything in this folder".
        .setIncludeFolders(true)
        // Folder SELECTION is gated by `folderSelectEnabled` (see the Props
        // doc): under drive.file a picked folder grants the folder object but
        // nothing beneath it, so offering it is a trap — only offer it once
        // the connection actually holds drive.readonly. Defaults to false, so
        // this call is a no-op change (still `false`) for every connection
        // until that scope ships.
        .setSelectFolderEnabled(folderSelectEnabled)

      const builder = new picker.PickerBuilder()
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
              const res = await connectorsApi.saveGoogleDriveFiles({
                files: mergePickedFiles(savedFilesRef.current, files),
              })
              const failure = syncFailureMessage(res.errors)
              if (failure) setError(failure)
              onSaved?.()
            } catch (e) {
              setError(toMessage(e))
            } finally {
              setBusy(false)
            }
          })()
        })

      // Under the drive.file scope, the Picker must be told which Cloud
      // project (app) to bind a picked file to — otherwise the file is
      // picked but never granted to this app, and the backend's later read
      // fails with "File not found". Skip the call entirely when app_id is
      // absent/empty rather than passing "" — Google may read an empty
      // string as a malformed app id rather than as unset.
      if (token.app_id) {
        builder.setAppId(token.app_id)
      }

      const built = builder.build()

      built.setVisible(true)
    } catch (e) {
      setError(toMessage(e))
    } finally {
      setBusy(false)
    }
  }, [apiKey, folderSelectEnabled, onSaved])

  /**
   * Disconnect one file. The endpoint replaces the stored list, so "remove" is
   * a save of everything except this id — the same call the Picker makes.
   *
   * The file stops being read, stops re-syncing and leaves the connected set.
   * Content already ingested into the corpus is NOT purged: `google_drive_sync`
   * keys `file_mtime`/`kg_file_mtime` by Drive file id and never records which
   * corpus document that id produced, so there is nothing to look the document
   * up by. Deleting the wrong document is far worse than leaving one, so this
   * does the half it can do correctly.
   */
  const handleRemoveFile = useCallback(
    async (id: string) => {
      const remaining = savedFilesRef.current.filter((f) => f.id !== id)
      // `removingId`, not `busy`: this is the delete's own progress, and it
      // belongs to the row that was clicked.
      setRemovingId(id)
      setError(null)
      try {
        await connectorsApi.saveGoogleDriveFiles({ files: remaining })
        onSaved?.()
      } catch (e) {
        setError(toMessage(e))
      } finally {
        setRemovingId(null)
      }
    },
    [onSaved],
  )

  return (
    <GoogleDrivePickerView
      savedFiles={savedFiles ?? []}
      configured={configured}
      busy={busy}
      error={error}
      onAddFiles={() => void handleAddFiles()}
      onRemoveFile={(id) => void handleRemoveFile(id)}
      removingId={removingId}
      folderContents={folderContents}
    />
  )
}

function toMessage(e: unknown): string {
  if (e instanceof ApiError) return apiErrorMessage(e.status, e.body)
  if (e instanceof Error) return e.message
  return String(e)
}

/** One-line summary of per-file sync failures returned in a 200 body. */
export function syncFailureMessage(
  errors: { name: string; error: string }[] | undefined,
): string | null {
  const list = (errors ?? []).filter(
    (e) => e && typeof e.error === "string" && e.error.length > 0,
  )
  if (list.length === 0) return null
  const first = `${list[0].name}: ${list[0].error}`
  return list.length === 1 ? first : `${first} (+${list.length - 1} more)`
}
