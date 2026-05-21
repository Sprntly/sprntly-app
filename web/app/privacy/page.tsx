import type { Metadata } from "next"
import { LegalDocument } from "../components/legal/LegalDocument"
import {
  PRIVACY_EFFECTIVE,
  PRIVACY_LAST_UPDATED,
  PRIVACY_SECTIONS,
} from "./content"

export const metadata: Metadata = {
  title: "Privacy Policy · Sprntly",
  description:
    "How Sprntly collects, uses, and protects personal information and customer data.",
}

export default function PrivacyPolicyPage() {
  return (
    <LegalDocument
      title="Sprntly Privacy Policy"
      effective={PRIVACY_EFFECTIVE}
      lastUpdated={PRIVACY_LAST_UPDATED}
      sections={PRIVACY_SECTIONS}
      contactEmail="privacy@sprntly.ai"
      contactLabel="privacy@sprntly.ai"
      sibling={{ href: "/terms", label: "Terms of Use" }}
    />
  )
}
