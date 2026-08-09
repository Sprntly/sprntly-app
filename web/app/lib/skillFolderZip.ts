/**
 * Folder upload → a zip the existing skill pipeline already understands.
 *
 * A browser can hand us a whole directory (`<input webkitdirectory>`), but
 * the upload endpoint deliberately takes ONE file — every guard the server
 * runs (member caps, hostile-path skips, the multi-skill split, per-skill
 * naming) is keyed on the zip format. So the folder is packed client-side
 * into an uncompressed ("stored") zip and sent down the very same path a
 * hand-zipped archive takes. No dependency: STORE-method zip writing is a
 * page of fixed-layout headers, and skills are markdown, so compression
 * would save nothing worth a library.
 *
 * Only .md files are packed. The server ignores everything else anyway, and
 * leaving images/binaries out is what keeps a big design folder under the
 * 20 MB upload cap.
 */

/** Mirror of the server's zip guards (skills.custom): member count and the
 *  upload byte cap it will be checked against on arrival. */
export const MAX_FOLDER_MD_FILES = 200

const MAX_ZIP_BYTES = 20 * 1024 * 1024

const CRC_TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

export function crc32(data: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < data.length; i++) c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

export type ZipEntry = { path: string; data: Uint8Array }

/** A STORE-method (no compression) zip of `entries`, paths kept verbatim.
 *  Fixed 1980-01-01 timestamps: the server reads content, never dates, and a
 *  stable byte stream means the same folder always hashes the same. */
export function buildStoredZip(entries: ZipEntry[]): Uint8Array {
  const encoder = new TextEncoder()
  const locals: Uint8Array[] = []
  const centrals: Uint8Array[] = []
  let offset = 0
  const DOS_DATE = (1980 - 1980) << 9 | (1 << 5) | 1 // 1980-01-01
  for (const { path, data } of entries) {
    const name = encoder.encode(path)
    const crc = crc32(data)
    const local = new Uint8Array(30 + name.length + data.length)
    const lv = new DataView(local.buffer)
    lv.setUint32(0, 0x04034b50, true)
    lv.setUint16(4, 20, true) // version needed
    lv.setUint16(6, 0x0800, true) // UTF-8 names
    lv.setUint16(8, 0, true) // method: store
    lv.setUint16(10, 0, true) // time
    lv.setUint16(12, DOS_DATE, true)
    lv.setUint32(14, crc, true)
    lv.setUint32(18, data.length, true) // compressed = raw for store
    lv.setUint32(22, data.length, true)
    lv.setUint16(26, name.length, true)
    lv.setUint16(28, 0, true) // extra len
    local.set(name, 30)
    local.set(data, 30 + name.length)
    locals.push(local)

    const central = new Uint8Array(46 + name.length)
    const cv = new DataView(central.buffer)
    cv.setUint32(0, 0x02014b50, true)
    cv.setUint16(4, 20, true) // made by
    cv.setUint16(6, 20, true) // version needed
    cv.setUint16(8, 0x0800, true)
    cv.setUint16(10, 0, true)
    cv.setUint16(12, 0, true)
    cv.setUint16(14, DOS_DATE, true)
    cv.setUint32(16, crc, true)
    cv.setUint32(20, data.length, true)
    cv.setUint32(24, data.length, true)
    cv.setUint16(28, name.length, true)
    cv.setUint32(42, offset, true) // local header offset
    central.set(name, 46)
    centrals.push(central)
    offset += local.length
  }
  const cdSize = centrals.reduce((n, c) => n + c.length, 0)
  const eocd = new Uint8Array(22)
  const ev = new DataView(eocd.buffer)
  ev.setUint32(0, 0x06054b50, true)
  ev.setUint16(8, entries.length, true)
  ev.setUint16(10, entries.length, true)
  ev.setUint32(12, cdSize, true)
  ev.setUint32(16, offset, true) // central directory offset
  const out = new Uint8Array(offset + cdSize + 22)
  let at = 0
  for (const part of [...locals, ...centrals, eocd]) {
    out.set(part, at)
    at += part.length
  }
  return out
}

export type FolderZipResult = { file: File; mdCount: number } | { error: string }

/** The relative path a directory pick reports for one file, "" when absent
 *  (a plain multi-file pick, or a test's synthetic File). */
function relativePath(f: File): string {
  const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath
  return typeof rel === "string" ? rel : ""
}

/** File → bytes, via Blob.arrayBuffer where the runtime has it and FileReader
 *  where it doesn't (older WebKit, jsdom). */
async function fileBytes(f: File): Promise<Uint8Array> {
  if (typeof f.arrayBuffer === "function") return new Uint8Array(await f.arrayBuffer())
  return await new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer))
    r.onerror = () => reject(r.error ?? new Error("read failed"))
    r.readAsArrayBuffer(f)
  })
}

/** Pack a picked folder's .md files into an uploadable zip File. Errors are
 *  returned, not thrown — they are user messages for the modal's inline slot,
 *  phrased to match the server's own wording for the same limits. */
export async function zipFolderFiles(picked: File[]): Promise<FolderZipResult> {
  const mdFiles = picked.filter((f) => f.name.toLowerCase().endsWith(".md"))
  if (mdFiles.length === 0) {
    return { error: "That folder has no .md files — a skill is a Markdown file." }
  }
  if (mdFiles.length > MAX_FOLDER_MD_FILES) {
    return {
      error: `That folder has ${mdFiles.length} .md files — the limit for one upload is ${MAX_FOLDER_MD_FILES}.`,
    }
  }
  const entries: ZipEntry[] = []
  for (const f of mdFiles) {
    const path = relativePath(f) || f.name
    entries.push({ path, data: await fileBytes(f) })
  }
  // The picked folder's own name leads every relative path; keep it — the
  // server unwraps a single top-level folder and uses its name as the
  // fallback identity for a root-level skill.
  const first = relativePath(mdFiles[0])
  const folderName = first.includes("/") ? first.split("/")[0] : "skills"
  const bytes = buildStoredZip(entries)
  if (bytes.length > MAX_ZIP_BYTES) {
    return { error: "File size exceeds the 20 MB limit. Please upload a smaller file." }
  }
  const file = new File([bytes.buffer as ArrayBuffer], `${folderName}.zip`, {
    type: "application/zip",
  })
  return { file, mdCount: mdFiles.length }
}
