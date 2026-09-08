/**
 * Which attachments the browser may read as text, and which must reach the
 * server as bytes.
 *
 * THIS IS AN ALLOW-LIST OF TEXT, and it used to be a deny-list of binaries —
 * which is the bug. `handleFileSelect` read anything not named `.pdf`,
 * `.pptx`, `.docx` or `.doc` with `FileReader.readAsText`, so every OTHER
 * binary format was decoded as if it were text. The result is mojibake, and
 * because mojibake is a NON-EMPTY string it lands in `content` as though
 * extraction had succeeded — so `resolveAttachmentRefs`, which only calls the
 * server parser when `content` is empty, never gets consulted at all.
 *
 * Observed: a .zip attached to a chat produced an answer describing "the ZIP
 * local file header (PK signature)" and concluding the archive held one file.
 * The model was reading the raw container bytes, transcribed as text. Nothing
 * in the archive was ever opened.
 *
 * The same trap had already been found once for spreadsheets — an .xlsx is
 * itself a zip — and patched by adding two extensions to the deny-list in ONE
 * of the two composers. That is why this is now a shared predicate: the two
 * lists had already drifted, and the one that missed `.xlsx` was silently
 * corrupting workbooks.
 *
 * Inverting it makes the failure mode safe by default. An unknown extension
 * now goes to the server, which either extracts real text or says plainly that
 * it cannot — instead of the client inventing text that is technically a
 * string and semantically noise.
 */

/**
 * Extensions whose bytes ARE their text. Everything else — documents,
 * spreadsheets, archives, images, anything unrecognised — keeps its `File` and
 * is parsed server-side (`/v1/ask/extract-file`), which is also where a .zip
 * gets expanded into its members.
 */
export const CLIENT_READABLE_TEXT_EXTENSIONS = [
  "txt",
  "md",
  "markdown",
  "csv",
  "tsv",
  "json",
  "yaml",
  "yml",
  "log",
  "html",
  "htm",
  "xml",
] as const

const TEXT_RE = new RegExp(
  `\\.(${CLIENT_READABLE_TEXT_EXTENSIONS.join("|")})$`,
  "i",
)

/**
 * True when the browser may `readAsText` this file.
 *
 * A name with NO extension returns false: it could be anything, and guessing
 * "text" is the guess that corrupts silently. The server can still read it.
 */
export function isClientReadableText(filename: string): boolean {
  return TEXT_RE.test(filename.trim())
}
