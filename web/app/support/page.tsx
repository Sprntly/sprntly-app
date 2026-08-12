import type { Metadata } from "next"
import { LegalDocument } from "../components/legal/LegalDocument"
import { SUPPORT_EMAIL, SUPPORT_SECTIONS } from "./content"

export const metadata: Metadata = {
  title: "Support · Sprntly",
  description:
    "How to reach Sprntly support: open a case by email, support hours, first-response SLA, and documentation.",
}

export default function SupportPage() {
  return (
    <LegalDocument
      title="Sprntly Support"
      sections={SUPPORT_SECTIONS}
      contactEmail={SUPPORT_EMAIL}
      contactLabel={SUPPORT_EMAIL}
      sibling={{ href: "/docs", label: "Documentation" }}
    />
  )
}
