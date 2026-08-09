// skillFolderZip: the client-side folder → stored-zip packer. The zip layout
// is asserted structurally (signatures, central directory, EOCD counts, CRC)
// because the consumer is Python's zipfile on the server — the bytes, not a
// JS unzipper, are the contract.
import { describe, expect, it } from "vitest"
import {
  MAX_FOLDER_MD_FILES,
  buildStoredZip,
  crc32,
  zipFolderFiles,
} from "../skillFolderZip"

const enc = new TextEncoder()

function u32(bytes: Uint8Array, at: number): number {
  return new DataView(bytes.buffer, bytes.byteOffset).getUint32(at, true)
}

function u16(bytes: Uint8Array, at: number): number {
  return new DataView(bytes.buffer, bytes.byteOffset).getUint16(at, true)
}

function indexOfBytes(haystack: Uint8Array, needle: Uint8Array): number {
  outer: for (let i = 0; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer
    }
    return i
  }
  return -1
}

describe("crc32", () => {
  it("matches the standard check value", () => {
    // The canonical CRC-32 test vector — any implementation drift fails here.
    expect(crc32(enc.encode("123456789"))).toBe(0xcbf43926)
  })
})

describe("buildStoredZip", () => {
  it("writes a well-formed stored zip: local headers, central directory, EOCD", () => {
    const a = enc.encode("# Alpha method\n")
    const b = enc.encode("# Beta method\n")
    const zip = buildStoredZip([
      { path: "skills/alpha/SKILL.md", data: a },
      { path: "skills/beta-skill.md", data: b },
    ])

    // Local file header at byte 0, store method, UTF-8 flag.
    expect(u32(zip, 0)).toBe(0x04034b50)
    expect(u16(zip, 8)).toBe(0) // method: store
    expect(u16(zip, 6)).toBe(0x0800) // UTF-8 names

    // EOCD is the last 22 bytes (no comment) and counts both entries.
    const eocd = zip.length - 22
    expect(u32(zip, eocd)).toBe(0x06054b50)
    expect(u16(zip, eocd + 8)).toBe(2)
    expect(u16(zip, eocd + 10)).toBe(2)

    // The central directory sits where the EOCD says it does.
    const cdOffset = u32(zip, eocd + 16)
    expect(u32(zip, cdOffset)).toBe(0x02014b50)
    // First central entry: correct CRC, sizes, and a local-header offset that
    // really points at a local header for the same file.
    expect(u32(zip, cdOffset + 16)).toBe(crc32(a))
    expect(u32(zip, cdOffset + 20)).toBe(a.length)
    expect(u32(zip, cdOffset + 24)).toBe(a.length)
    const localOffset = u32(zip, cdOffset + 42)
    expect(u32(zip, localOffset)).toBe(0x04034b50)

    // Paths and content are stored verbatim.
    expect(indexOfBytes(zip, enc.encode("skills/alpha/SKILL.md"))).toBeGreaterThanOrEqual(0)
    expect(indexOfBytes(zip, a)).toBeGreaterThanOrEqual(0)
    expect(indexOfBytes(zip, enc.encode("skills/beta-skill.md"))).toBeGreaterThanOrEqual(0)
  })
})

function folderFile(relPath: string, content: string): File {
  const name = relPath.split("/").pop()!
  const file = new File([content], name, { type: "text/markdown" })
  Object.defineProperty(file, "webkitRelativePath", { value: relPath })
  return file
}

describe("zipFolderFiles", () => {
  it("packs only the .md files, keeping the folder-relative paths", async () => {
    const result = await zipFolderFiles([
      folderFile("My Skills/SKILL.md", "# Root"),
      folderFile("My Skills/governance-skill.md", "# Gov"),
      folderFile("My Skills/logo.png", "not markdown"),
    ])
    if ("error" in result) throw new Error(result.error)
    expect(result.mdCount).toBe(2)
    // Named after the picked folder, so the server's single-wrapper unwrap
    // and root-skill naming fallback both see the folder's own name.
    expect(result.file.name).toBe("My Skills.zip")
    const bytes = new Uint8Array(await result.file.arrayBuffer())
    expect(indexOfBytes(bytes, enc.encode("My Skills/SKILL.md"))).toBeGreaterThanOrEqual(0)
    expect(indexOfBytes(bytes, enc.encode("My Skills/governance-skill.md"))).toBeGreaterThanOrEqual(0)
    expect(indexOfBytes(bytes, enc.encode("logo.png"))).toBe(-1)
  })

  it("errors on a folder with no markdown at all", async () => {
    const result = await zipFolderFiles([folderFile("assets/logo.png", "png")])
    expect("error" in result && result.error).toMatch(/no \.md files/)
  })

  it("errors past the member cap instead of shipping a doomed upload", async () => {
    const files = Array.from({ length: MAX_FOLDER_MD_FILES + 1 }, (_, i) =>
      folderFile(`big/skill-${i}.md`, "text"),
    )
    const result = await zipFolderFiles(files)
    expect("error" in result && result.error).toMatch(/limit for one upload/)
  })
})
