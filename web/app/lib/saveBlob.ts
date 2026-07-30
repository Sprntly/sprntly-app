/**
 * Trigger a browser download of an already-fetched Blob.
 *
 * Deliberately dependency-free, unlike the lazy `file-saver` import in
 * lib/prdExport.ts. These callers run on a download the SERVER has already
 * produced (a report PDF costs a headless-Chromium render), and a dynamic
 * import is a second thing that can fail on its own — a chunk that 404s after a
 * rebuild throws away a file the user has already paid for. An anchor plus an
 * object URL is the whole of what file-saver does for this case, with nothing
 * left to load at click time.
 *
 * Shared by the in-app report viewer and the public `/r/<token>` page so a
 * download behaves identically on both.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.rel = "noopener"
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick — revoking synchronously can cancel the download in
  // some browsers before they have read the object URL.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
