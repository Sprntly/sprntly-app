// View tests for the Settings → Business Context pane.
// Same node-env SSR pattern as TeamSettings.test.tsx / SecuritySettings.test.tsx:
// the View is pure, so we render it with renderToStaticMarkup and assert markup.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  BusinessContextSettingsView,
  CompanyShapeSettingsView,
  buildLayers,
  type BusinessContextSettingsViewProps,
  type CompanyShapeSettingsViewProps,
} from "../BusinessContextSettings"
import type { BcLeaf, BusinessContextDoc } from "../../../../../lib/api"

function leaf(value: unknown, src: BcLeaf["src"] = "inferred", extra: Partial<BcLeaf> = {}): BcLeaf {
  return { value, src, conf: "high", as_of: "2026-06-01", evidence: null, ...extra }
}

const emptyLeaf = (): BcLeaf => ({ value: null, src: "unknown", conf: null, as_of: null, evidence: null })

function makeDoc(): BusinessContextDoc {
  return {
    identity: {
      legal_name: leaf("Acme Inc.", "given"),
      also_known_as: emptyLeaf(),
      website: leaf("https://acme.example", "given"),
      one_liner: leaf("Invoicing for freelancers", "inferred", { evidence: "Homepage hero" }),
      industry: leaf("B2B SaaS", "inferred"),
      sub_vertical: emptyLeaf(),
      company_size: emptyLeaf(),
      stage: leaf("Growth", "inferred"),
      hq_geography: emptyLeaf(),
      markets_served: leaf(["US", "EU"], "inferred"),
    },
    business_model: {
      model_type: leaf("Subscription", "inferred"),
      revenue_model: emptyLeaf(),
      pricing_model: emptyLeaf(),
      who_pays: leaf("Finance lead", "inferred"),
      who_uses: emptyLeaf(),
      monetization_unit: emptyLeaf(),
      unit_economics_shape: emptyLeaf(),
      good_outcome: emptyLeaf(),
    },
    users_segments: { segments: [], primary_segment: emptyLeaf() },
    product_value: {
      what_it_does: leaf("Sends and tracks invoices", "user"),
      core_value_moments: emptyLeaf(),
      activation_definition: emptyLeaf(),
      key_features: emptyLeaf(),
      platforms: emptyLeaf(),
    },
    market_competition: { category: leaf("Invoicing", "inferred"), main_alternatives: emptyLeaf(), positioning_angle: emptyLeaf() },
    goals_strategy: { stated_goal: emptyLeaf(), north_star: emptyLeaf(), current_priorities: emptyLeaf(), known_constraints: emptyLeaf() },
    vocabulary: { terms: [] },
    meta: {
      created: emptyLeaf(),
      last_refreshed: emptyLeaf(),
      refresh_trigger: emptyLeaf(),
      overall_confidence: emptyLeaf(),
      sources: [],
    },
    version: 3,
  }
}

function valuesFor(doc: BusinessContextDoc): Record<string, string> {
  const out: Record<string, string> = {}
  for (const layer of buildLayers(doc)) {
    for (const f of layer.fields) {
      const v = f.leaf.value
      out[f.path] = v == null ? "" : Array.isArray(v) ? v.join(", ") : String(v)
    }
  }
  return out
}

function render(override: Partial<BusinessContextSettingsViewProps> = {}): string {
  const doc = override.doc === undefined ? makeDoc() : override.doc
  const defaults: BusinessContextSettingsViewProps = {
    loading: false,
    loadError: null,
    doc,
    values: doc ? valuesFor(doc) : {},
    canEdit: true,
    saving: false,
    saved: false,
    saveError: null,
    refreshing: false,
    refreshError: null,
    onChangeField: () => {},
    onSave: () => {},
    onRefresh: () => {},
  }
  return renderToStaticMarkup(
    React.createElement(BusinessContextSettingsView, { ...defaults, ...override }),
  )
}

describe("BusinessContextSettingsView — loaded doc", () => {
  it("renders the fetched leaf values as form fields", () => {
    const html = render()
    expect(html).toContain("Acme Inc.")
    expect(html).toContain("Invoicing for freelancers")
    expect(html).toContain("Sends and tracks invoices")
    // list values are joined for editing
    expect(html).toContain("US, EU")
  })

  it("shows the layer titles", () => {
    const html = render()
    expect(html).toContain("Identity")
    expect(html).toContain("Business model")
    expect(html).toContain("Product &amp; value")
    expect(html).toContain("Vocabulary")
  })

  it("shows provenance (src/conf) read-only next to fields", () => {
    const html = render()
    expect(html).toContain("Given")
    expect(html).toContain("Inferred")
    expect(html).toContain("high confidence")
  })

  it("surfaces evidence snippets when present", () => {
    const html = render()
    expect(html).toContain("Evidence: Homepage hero")
  })
})

describe("BusinessContextSettingsView — admin vs read-only", () => {
  it("admin sees Save", () => {
    const html = render({ canEdit: true })
    expect(html).toContain("Save business context")
    // The Version/Regenerate toolbar was removed; no Regenerate button is rendered.
    expect(html).not.toContain(">Regenerate<")
  })

  it("non-admin gets a read-only view (no Save, no Regenerate)", () => {
    const html = render({ canEdit: false })
    expect(html).not.toContain("Save business context")
    expect(html).not.toContain(">Regenerate<")
    expect(html).toContain("Only admins can edit")
    // fields are present but disabled for non-admins
    expect(html).toContain("disabled")
  })
})

describe("BusinessContextSettingsView — empty / 404 state", () => {
  it("admin sees the generate prompt + button when doc is null (404)", () => {
    const html = render({ doc: null })
    expect(html).toContain("hasn&#x27;t been generated yet")
    expect(html).toContain("Generate business context")
  })

  it("non-admin sees the not-generated message but no generate button", () => {
    const html = render({ doc: null, canEdit: false })
    expect(html).toContain("hasn&#x27;t been generated yet")
    expect(html).not.toContain("Generate business context")
    expect(html).toContain("Ask an admin")
  })
})

describe("BusinessContextSettingsView — chrome states", () => {
  it("shows loading", () => {
    expect(render({ loading: true })).toContain("Loading business context")
  })

  it("shows load error", () => {
    expect(render({ loadError: "API 500" })).toContain("API 500")
  })

  it("shows save success message", () => {
    expect(render({ saved: true })).toContain("Business context saved")
  })

  it("disables Save while saving", () => {
    expect(render({ saving: true })).toContain("Saving…")
  })
})

// ── company-shape section (relocated from the onboarding business-context step) ──
function renderShape(
  override: Partial<CompanyShapeSettingsViewProps> = {},
): string {
  const defaults: CompanyShapeSettingsViewProps = {
    loading: false,
    industry: "B2B SaaS",
    businessType: "SaaS",
    techStack: ["React"],
    canEdit: true,
    saving: false,
    saved: false,
    error: null,
    onChangeIndustry: () => {},
    onChangeBusinessType: () => {},
    onToggleTechStack: () => {},
    onSave: () => {},
  }
  return renderToStaticMarkup(
    React.createElement(CompanyShapeSettingsView, { ...defaults, ...override }),
  )
}

describe("CompanyShapeSettingsView — relocated company-shape fields", () => {
  it("renders the Industry / Business type / Tech stack controls", () => {
    const html = renderShape()
    expect(html).toContain("Company shape")
    expect(html).toContain('data-field="industry"')
    expect(html).toContain('data-field="businessType"')
    expect(html).toContain('data-field="techStack"')
    expect(html).toContain("Tech stack")
    expect(html).toContain("data-bc-company-shape")
  })

  it("shows the current values selected", () => {
    const html = renderShape({ industry: "Fintech", businessType: "Marketplace" })
    // The selected option is rendered as selected in the static markup.
    expect(html).toContain("Fintech")
    expect(html).toContain("Marketplace")
  })

  it("admin sees Save; non-admin gets disabled controls and no Save", () => {
    expect(renderShape({ canEdit: true })).toContain("Save company shape")
    const ro = renderShape({ canEdit: false })
    expect(ro).not.toContain("Save company shape")
    expect(ro).toContain("disabled")
  })

  it("wires onSave to the form's onSubmit", () => {
    const onSave = vi.fn()
    const props: CompanyShapeSettingsViewProps = {
      loading: false,
      industry: "B2B SaaS",
      businessType: "SaaS",
      techStack: [],
      canEdit: true,
      saving: false,
      saved: false,
      error: null,
      onChangeIndustry: () => {},
      onChangeBusinessType: () => {},
      onToggleTechStack: () => {},
      onSave,
    }
    type Node = { props?: { onSubmit?: (e: unknown) => void; children?: unknown }; type?: unknown }
    function findForm(node: unknown): ((e: unknown) => void) | null {
      if (!node || typeof node !== "object") return null
      const n = node as Node
      if (n.type === "form" && n.props?.onSubmit) return n.props.onSubmit
      const kids = n.props?.children
      const arr = Array.isArray(kids) ? kids : kids != null ? [kids] : []
      for (const k of arr) {
        const found = findForm(k)
        if (found) return found
      }
      return null
    }
    const rendered = (
      CompanyShapeSettingsView as (p: CompanyShapeSettingsViewProps) => unknown
    )(props)
    const submit = findForm(rendered)
    expect(submit).toBeTypeOf("function")
    submit?.({ preventDefault() {} })
    expect(onSave).toHaveBeenCalledTimes(1)
  })
})

describe("BusinessContextSettingsView — Save wiring", () => {
  it("calls onSave when the form is submitted", () => {
    // The View is pure; assert it wires onSave to the form's onSubmit by
    // building the element and invoking the handler through props.
    const onSave = vi.fn()
    const doc = makeDoc()
    const props: BusinessContextSettingsViewProps = {
      loading: false,
      loadError: null,
      doc,
      values: valuesFor(doc),
      canEdit: true,
      saving: false,
      saved: false,
      saveError: null,
      refreshing: false,
      refreshError: null,
      onChangeField: () => {},
      onSave,
      onRefresh: () => {},
    }
    const el = React.createElement(BusinessContextSettingsView, props)
    // Walk the rendered tree to find the <form> and fire its onSubmit.
    type Node = { props?: { onSubmit?: (e: unknown) => void; children?: unknown }; type?: unknown }
    function findForm(node: unknown): ((e: unknown) => void) | null {
      if (!node || typeof node !== "object") return null
      const n = node as Node
      if (n.type === "form" && n.props?.onSubmit) return n.props.onSubmit
      const kids = n.props?.children
      const arr = Array.isArray(kids) ? kids : kids != null ? [kids] : []
      for (const k of arr) {
        const found = findForm(k)
        if (found) return found
      }
      return null
    }
    // Render to an element tree we can traverse via the component's output.
    const rendered = (BusinessContextSettingsView as (p: BusinessContextSettingsViewProps) => unknown)(props)
    const submit = findForm(rendered)
    expect(submit).toBeTypeOf("function")
    submit?.({ preventDefault() {} })
    expect(onSave).toHaveBeenCalledTimes(1)
    void el
  })
})
