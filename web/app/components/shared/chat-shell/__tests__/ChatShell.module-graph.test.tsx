// @vitest-environment node
//
// Negative-import gate (AC7): ChatShell.tsx and every source module in the
// chat-shell/ folder statically import NONE of the project-only leaf modules
// (useRealtimeChannel, mentions, avatarColor). Project-only leaves arrive only
// as descriptor-injected nodes constructed by the project hosts — never pulled
// into the shell's own module graph — so main's chunk stays clean of realtime,
// mention, and avatar code.
import { readFileSync, readdirSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { dirname, join } from "node:path"
import { describe, expect, it } from "vitest"

const chatShellDir = join(dirname(fileURLToPath(import.meta.url)), "..")

// Every non-test source file that ships in the shell's module graph.
const sourceFiles = readdirSync(chatShellDir).filter(
  (f) => (f.endsWith(".ts") || f.endsWith(".tsx")) && !f.endsWith(".d.ts"),
)

const IMPORT_RE = /(?:import|export)[^'"]*from\s*['"]([^'"]+)['"]|import\s*['"]([^'"]+)['"]/g

function importSources(src: string): string[] {
  const out: string[] = []
  let m: RegExpExecArray | null
  while ((m = IMPORT_RE.exec(src)) !== null) {
    out.push(m[1] ?? m[2])
  }
  return out
}

const FORBIDDEN = [/useRealtimeChannel/, /\/mentions(?:$|['"./])/, /avatarColor/, /screens\/app\/projects\//]

describe("ChatShell module graph", () => {
  it("test_chatshell_does_not_import_project_leaves", () => {
    expect(sourceFiles).toContain("ChatShell.tsx")
    for (const file of sourceFiles) {
      const src = readFileSync(join(chatShellDir, file), "utf8")
      for (const spec of importSources(src)) {
        for (const bad of FORBIDDEN) {
          expect(
            bad.test(spec),
            `${file} must not import a project-only leaf, but imports "${spec}"`,
          ).toBe(false)
        }
      }
    }
  })
})
