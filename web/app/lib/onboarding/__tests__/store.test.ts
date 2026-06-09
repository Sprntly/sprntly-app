// Tests for createWorkspace slug generation. The company slug is an opaque,
// name-independent token (see generateSlug); these assert it always satisfies
// the backend `companies_slug_format` CHECK regardless of the company name,
// that adversarial names never throw a format error, and that a UNIQUE
// collision (Postgres 23505) regenerates a fresh token and retries.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const SLUG_FORMAT = /^[a-z0-9][a-z0-9_-]{1,62}$/

// ---- Mock supabase client -------------------------------------------------
// A minimal chainable stub. We control the companies insert outcome via
// `companyInsertResults` (a queue of per-attempt results), record every slug
// passed to the companies insert, and treat all other tables as benign.

type InsertResult = { data: Record<string, unknown> | null; error: { code?: string } | null }

let insertedSlugs: string[]
let companyInsertResults: InsertResult[]

function makeCompaniesBuilder() {
  const builder: Record<string, unknown> = {}
  builder.insert = (row: Record<string, unknown>) => {
    insertedSlugs.push(String(row.slug))
    const result = companyInsertResults.shift() ?? {
      data: { id: "company-1", slug: row.slug, display_name: row.display_name },
      error: null,
    }
    // .insert(...).select(...).single()
    return {
      select: () => ({ single: async () => result }),
    }
  }
  return builder
}

function makeGenericBuilder() {
  // Covers company_members.insert (awaited for { error }), profiles.update().eq(),
  // and products.* (insert/select/single). Every chainable also resolves to a
  // benign { error: null } so a plain `await builder.insert(...)` works too.
  const productRow = {
    data: { id: "product-1", company_id: "company-1", name: "P", is_primary: true },
    error: null,
  }
  const builder: Record<string, unknown> = {}
  const single = async () => productRow
  const maybeSingle = async () => ({ data: null, error: null })
  builder.select = () => builder
  builder.eq = () => builder
  builder.single = single
  builder.maybeSingle = maybeSingle
  builder.update = () => builder
  // insert() must be both awaitable (company_members) and chainable (products).
  const insertResult = Promise.resolve({ error: null }) as Promise<{ error: null }> &
    Record<string, unknown>
  insertResult.select = () => builder
  builder.insert = () => insertResult
  return builder
}

const supabaseStub = {
  from(table: string) {
    if (table === "companies") return makeCompaniesBuilder()
    return makeGenericBuilder()
  },
}

vi.mock("../../supabase/client", () => ({
  getSupabase: () => supabaseStub,
}))

import { createWorkspace } from "../store"

function baseInput(companyName: string) {
  return {
    companyName,
    productName: "Primary",
    industry: "B2B SaaS",
    stage: "Growth",
    businessType: "SaaS",
    userId: "user-1",
  }
}

beforeEach(() => {
  insertedSlugs = []
  companyInsertResults = []
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("createWorkspace slug generation", () => {
  const adversarial: Array<[string, string]> = [
    ["leading hyphen", "-Acme"],
    ["leading hash", "#Launch"],
    ["leading punctuation + space", "· Beta"],
    ["emoji only", "🚀🚀🚀"],
    ["empty string", ""],
    ["200-char name", "A".repeat(200)],
    ["single char", "A"],
    ["whitespace only", "   "],
  ]

  for (const [label, name] of adversarial) {
    it(`produces a valid, name-independent slug for ${label}`, async () => {
      await createWorkspace(baseInput(name))
      expect(insertedSlugs).toHaveLength(1)
      const slug = insertedSlugs[0]
      // Always satisfies the backend CHECK — never throws a format (23514) error.
      expect(slug).toMatch(SLUG_FORMAT)
      // Independent of the name: the raw name does not leak into the slug.
      const trimmed = name.trim().toLowerCase()
      if (trimmed.length > 1) {
        expect(slug).not.toContain(trimmed)
      }
    })
  }

  it("uses display_name for the free-text name, not the slug", async () => {
    let capturedRow: Record<string, unknown> | null = null
    const orig = supabaseStub.from
    // Spy on the companies insert payload.
    vi.spyOn(supabaseStub, "from").mockImplementation((table: string) => {
      if (table === "companies") {
        return {
          insert: (row: Record<string, unknown>) => {
            capturedRow = row
            insertedSlugs.push(String(row.slug))
            return { select: () => ({ single: async () => ({
              data: { id: "company-1", slug: row.slug, display_name: row.display_name },
              error: null,
            }) }) }
          },
        }
      }
      return makeGenericBuilder()
    })
    await createWorkspace(baseInput("  -Acme  "))
    expect(capturedRow).not.toBeNull()
    expect((capturedRow as Record<string, unknown>).display_name).toBe("-Acme")
    expect((capturedRow as Record<string, unknown>).slug).toMatch(SLUG_FORMAT)
    supabaseStub.from = orig
  })

  it("regenerates a fresh token and retries on a 23505 unique collision", async () => {
    // First two attempts collide, third succeeds.
    companyInsertResults = [
      { data: null, error: { code: "23505" } },
      { data: null, error: { code: "23505" } },
      { data: { id: "company-1", slug: "ok", display_name: "Acme" }, error: null },
    ]
    await createWorkspace(baseInput("Acme"))
    expect(insertedSlugs).toHaveLength(3)
    // Each retry used a FRESH token, not an appended suffix of the prior one.
    expect(new Set(insertedSlugs).size).toBe(3)
    for (const slug of insertedSlugs) expect(slug).toMatch(SLUG_FORMAT)
  })

  it("throws (without exhausting forever) after 5 collisions", async () => {
    companyInsertResults = Array.from({ length: 5 }, () => ({
      data: null,
      error: { code: "23505" } as { code: string },
    }))
    await expect(createWorkspace(baseInput("Acme"))).rejects.toThrow(/Could not create workspace/)
    expect(insertedSlugs).toHaveLength(5)
  })

  it("rethrows immediately on a non-collision error (e.g. format 23514)", async () => {
    companyInsertResults = [{ data: null, error: { code: "23514" } }]
    await expect(createWorkspace(baseInput("Acme"))).rejects.toBeTruthy()
    // Did not retry on a non-23505 error.
    expect(insertedSlugs).toHaveLength(1)
  })

  // RLS precondition (see migration 20260608130000_company_members_rls_first_owner):
  // the company_members INSERT policy only allows the first-owner insert when the
  // companies row already exists (created_by = self, 0 members). createWorkspace
  // MUST therefore insert the companies row BEFORE the company_members row. If this
  // ordering ever regresses, the policy would reject legitimate onboarding inserts.
  it("inserts the companies row before the company_members row (first-owner RLS order)", async () => {
    const insertOrder: string[] = []
    const orig = supabaseStub.from
    vi.spyOn(supabaseStub, "from").mockImplementation((table: string) => {
      if (table === "companies") {
        return {
          insert: (row: Record<string, unknown>) => {
            insertOrder.push("companies")
            insertedSlugs.push(String(row.slug))
            return { select: () => ({ single: async () => ({
              data: { id: "company-1", slug: row.slug, created_by: row.created_by },
              error: null,
            }) }) }
          },
        }
      }
      if (table === "company_members") {
        const result = Promise.resolve({ error: null }) as Promise<{ error: null }> &
          Record<string, unknown>
        return {
          insert: (row: Record<string, unknown>) => {
            insertOrder.push("company_members")
            // The first-owner row is the creator as owner.
            expect(row.user_id).toBe("user-1")
            expect(row.role).toBe("owner")
            return result
          },
        }
      }
      return makeGenericBuilder()
    })

    await createWorkspace(baseInput("Acme"))

    const companiesIdx = insertOrder.indexOf("companies")
    const memberIdx = insertOrder.indexOf("company_members")
    expect(companiesIdx).toBeGreaterThanOrEqual(0)
    expect(memberIdx).toBeGreaterThan(companiesIdx)
    supabaseStub.from = orig
  })
})
