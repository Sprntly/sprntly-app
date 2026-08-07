/**
 * A partially-mocked module that the code under test imports DYNAMICALLY is a
 * silent hole in a whole test file.
 *
 * THE DEFECT THIS EXISTS FOR (2026-08-07, PR #1109 / T5). A ChatScreen suite
 * mocked `lib/api` with a factory whose `conversationsApi` was missing `create`.
 * `chatPersistence` resolves that module through an injected, DYNAMIC
 * `getApi()` and calls `api.create(...)` on the first turn of a tab. With
 * `create` absent the call blew up, and `chatPersistence` swallows persistence
 * failures on purpose (`.catch`, so a DB hiccup never breaks the UI) — so the
 * tab got NO conversation id and EVERY turn in that file was silently dropped.
 * Nothing turned red. Every persistence assertion in the file was vacuous, and
 * the suite reported success.
 *
 * WHY A STATIC SCAN AND NOT A RUNTIME HOOK. A setup-file hook that hardened
 * every `vi.mock` would touch all ~900 mock sites at once — a large blast
 * radius for a guard, and the kind of change that gets reverted rather than
 * fixed. This reads source text instead: it costs milliseconds, it cannot flake,
 * and it is wrong only in the quiet direction (it can miss, it cannot invent).
 *
 * THE RULE, and why it has no false positives. If a test file mocks `lib/api`
 * and supplies a `conversationsApi` object at all, then any module resolving
 * that same specifier — including `chatPersistence`'s dynamic `getApi()` — sees
 * exactly that object. So it must carry every method `chatPersistence` calls.
 * A file that does not mock `conversationsApi` is not flagged: that is a
 * different (and much noisier) question, and it is recorded as this check's
 * blind spot rather than guessed at.
 *
 * COVERAGE BY CONSTRUCTION. The required-method list is DERIVED from
 * `chatPersistence.ts` at test time, not written down here. Add an
 * `api.rename(...)` call to that module tomorrow and every partial mock in the
 * repo starts failing this check on the same run — nobody has to remember to
 * update a list.
 */
import fs from "node:fs"
import path from "node:path"

import { describe, expect, it } from "vitest"

const APP_ROOT = path.resolve(__dirname, "..", "..")
const CHAT_PERSISTENCE = path.join(APP_ROOT, "lib", "chatPersistence.ts")

/** Every method `chatPersistence` calls on the api it resolves dynamically. */
function requiredApiMethods(): string[] {
  const src = fs.readFileSync(CHAT_PERSISTENCE, "utf8")
  const names = new Set<string>()
  for (const m of src.matchAll(/\bapi\.([A-Za-z_$][\w$]*)\s*\(/g)) names.add(m[1])
  return [...names].sort()
}

function testFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === ".next") continue
      testFiles(full, out)
    } else if (/\.test\.tsx?$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

/**
 * The `conversationsApi: { ... }` object literal inside a `vi.mock` factory,
 * with its keys — or null when the file supplies no such literal.
 *
 * Brace-matched rather than regexed to the closing brace: a nested object (a
 * `mockResolvedValue({ id: 1 })`) would end a lazy regex early and under-report
 * keys, which would make this check fire on files that are actually fine. The
 * only acceptable direction for this parser to be wrong is silence.
 */
function conversationsApiKeys(src: string): string[] | null {
  const marker = /\bconversationsApi\s*:\s*\{/g
  const found = marker.exec(src)
  if (!found) return null
  let depth = 1
  let i = found.index + found[0].length
  const start = i
  while (i < src.length && depth > 0) {
    if (src[i] === "{") depth++
    else if (src[i] === "}") depth--
    i++
  }
  const body = src.slice(start, i - 1)
  // A spread (`...actual.conversationsApi`) means the literal's real key set is
  // not knowable from source. Report "unknown" rather than guessing — a guess
  // here is a false positive, and false positives are how this check dies.
  if (body.includes("...")) return null

  const keys: string[] = []
  let inner = 0
  for (let j = 0; j < body.length; j++) {
    const ch = body[j]
    if (ch === "{" || ch === "(" || ch === "[") {
      inner++
      continue
    }
    if (ch === "}" || ch === ")" || ch === "]") {
      inner--
      continue
    }
    if (inner !== 0) continue
    if (j !== 0 && !/[\s,]/.test(body[j - 1])) continue
    // `name:` (longhand), `name,` / `name` at the end (SHORTHAND), `name(`
    // (method shorthand). Missing the shorthand form is not a theoretical gap:
    // the first draft of this parser only matched `name:` and reported
    // ChatScreen.next-prompts as missing `addTurn` when the file plainly has
    // `addTurn,` on its own line. One false positive out of one hit.
    const key = /^([A-Za-z_$][\w$]*)\s*(?::|,|\(|$)/.exec(body.slice(j))
    if (key) keys.push(key[1])
  }
  return keys
}

/** Does this file mock the `lib/api` module (by any relative specifier)? */
function mocksLibApi(src: string): boolean {
  return /vi\.mock\(\s*["'][^"']*\blib\/api["']/.test(src)
}

// ---------------------------------------------------------------------------
// Which test files actually EXERCISE chatPersistence.
//
// Without this, the check flags every partial `conversationsApi` mock in the
// repo — and the first run of this file did exactly that, naming four files of
// which only ONE renders a component that reaches chatPersistence. The other
// three (a chat LIST screen, a command palette, a guest viewer) mock a narrow
// slice on purpose and are entirely correct to. Shipping that version would
// have been three false positives out of four, on a check whose whole selling
// point is that it does not cry wolf.
//
// So the module graph is computed instead of guessed: a test file is in scope
// only if something it imports transitively imports `lib/chatPersistence`.
// ---------------------------------------------------------------------------

const SOURCE_RE = /\.tsx?$/

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (["node_modules", ".next", "__tests__"].includes(entry.name)) continue
      sourceFiles(full, out)
    } else if (SOURCE_RE.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

/** Resolve a relative specifier to a real file, trying the usual extensions. */
function resolveImport(fromFile: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null
  const base = path.resolve(path.dirname(fromFile), spec)
  for (const cand of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ]) {
    if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return cand
  }
  return null
}

function relativeImports(file: string, src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/from\s+["'](\.[^"']*)["']/g)) {
    const resolved = resolveImport(file, m[1])
    if (resolved) out.push(resolved)
  }
  return out
}

/** Every app module that transitively imports chatPersistence, plus itself. */
function modulesReachingChatPersistence(): Set<string> {
  const files = sourceFiles(APP_ROOT)
  const imports = new Map<string, string[]>()
  for (const f of files) imports.set(f, relativeImports(f, fs.readFileSync(f, "utf8")))

  const reaching = new Set<string>([CHAT_PERSISTENCE])
  let grew = true
  while (grew) {
    grew = false
    for (const [file, deps] of imports) {
      if (reaching.has(file)) continue
      if (deps.some((d) => reaching.has(d))) {
        reaching.add(file)
        grew = true
      }
    }
  }
  return reaching
}

/** Does the factory rebuild the module from the real one (`{ ...actual }`)? */
function spreadsTheRealModule(src: string): boolean {
  return /importOriginal|importActual/.test(src)
}

describe("partial mocks of dynamically-imported modules", () => {
  const required = requiredApiMethods()
  const files = testFiles(APP_ROOT)

  it("derives the required methods from chatPersistence, not from a hardcoded list", () => {
    // Non-vacuity pin. If the derivation silently returned [] — the file moved,
    // the call shape changed — every check below would pass while checking
    // nothing, which is the exact failure this file exists to prevent.
    expect(fs.existsSync(CHAT_PERSISTENCE)).toBe(true)
    expect(required).toContain("create")
    expect(required).toContain("addTurn")
  })

  it("finds the test suite it is supposed to scan", () => {
    // Same argument on the other input. A broken walk means zero files scanned
    // and a green check over an unread question.
    expect(files.length).toBeGreaterThan(200)
    expect(files.filter((f) => mocksLibApi(fs.readFileSync(f, "utf8"))).length)
      .toBeGreaterThan(20)
  })

  it("the key parser survives nested objects", () => {
    // Validated against the real shape: `mockResolvedValue({ id: 1 })` contains
    // braces, so a naive parser stops at the first `}` and reports one key.
    const sample = `{
      conversationsApi: {
        create: vi.fn().mockResolvedValue({ id: 1 }),
        addTurn: vi.fn().mockResolvedValue({}),
        byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      },
    }`
    expect(conversationsApiKeys(sample)).toEqual(["create", "addTurn", "byPrd"])
  })

  it("the key parser reads SHORTHAND properties", () => {
    // Regression pin for this file's own first-draft false positive: a mock
    // written `addTurn,` (shorthand) was reported as missing addTurn. A guard
    // that cries wolf once gets ignored; twice, deleted.
    const shorthand = `{
      conversationsApi: {
        create: vi.fn().mockResolvedValue({ id: 11 }),
        addTurn,
      },
    }`
    expect(conversationsApiKeys(shorthand)).toEqual(["create", "addTurn"])
  })

  it("treats a spread mock as unknown rather than guessing", () => {
    const spread = `{
      conversationsApi: { ...actual.conversationsApi, create: vi.fn() },
    }`
    expect(conversationsApiKeys(spread)).toBeNull()
  })

  it("the parser reports a MISSING method as missing", () => {
    // The known-bad input, in the suite: the #1109 shape, with `create` gone.
    const bad = `{
      conversationsApi: {
        addTurn: vi.fn().mockResolvedValue({}),
        byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
      },
    }`
    expect(conversationsApiKeys(bad)).not.toContain("create")
  })

  it("narrows to the files that actually exercise chatPersistence", () => {
    // Non-vacuity pin #3, and the one that keeps this check honest. If the
    // graph walk returned only chatPersistence itself, the scope would be empty
    // and the check below would pass over every file in the repo.
    const reaching = modulesReachingChatPersistence()
    expect(reaching.size).toBeGreaterThan(1)
    expect(
      [...reaching].some((f) => f.endsWith(path.join("app", "ChatScreen.tsx"))),
    ).toBe(true)
  })

  it("no test file mocks conversationsApi without every method chatPersistence calls", () => {
    const reaching = modulesReachingChatPersistence()
    const offenders: string[] = []
    for (const file of files) {
      const src = fs.readFileSync(file, "utf8")
      if (!mocksLibApi(src)) continue
      if (spreadsTheRealModule(src)) continue
      // Only files that render something reaching chatPersistence. A chat LIST
      // screen or a command palette may mock a narrow conversationsApi slice
      // and be completely correct; flagging those would be noise, and noise is
      // how a guard gets deleted.
      if (!relativeImports(file, src).some((d) => reaching.has(d))) continue
      const keys = conversationsApiKeys(src)
      if (keys === null) continue
      const missing = required.filter((m) => !keys.includes(m))
      if (missing.length) {
        offenders.push(
          `${path.relative(APP_ROOT, file)} — conversationsApi mock is missing ` +
            `${missing.join(", ")} (has: ${keys.join(", ") || "nothing"})`,
        )
      }
    }
    expect(
      offenders,
      "These files mock `lib/api` with a PARTIAL `conversationsApi`. " +
        "`chatPersistence` resolves that same module through a dynamic " +
        `getApi() and calls ${required.join("/")} on it; a missing method ` +
        "throws into chatPersistence's deliberate `.catch`, so the tab never " +
        "gets a conversation id and EVERY turn in the file is silently " +
        "dropped — with no test turning red. That is how #1109 shipped a whole " +
        "suite of vacuous persistence assertions.\n" +
        "Fix: add the missing method(s) to the mock, or build the factory from " +
        "`importOriginal()` and override only what you need.\n\n" +
        offenders.join("\n"),
    ).toEqual([])
  })
})
