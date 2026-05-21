import type { Metadata } from "next"
import Link from "next/link"
import { LegalDocument } from "../components/legal/LegalDocument"
import { publicPath } from "../lib/public-path"
import {
  TERMS_EFFECTIVE,
  TERMS_LAST_UPDATED,
  TERMS_SECTIONS,
} from "./content"

export const metadata: Metadata = {
  title: "Terms of Use · Sprntly",
  description: "Terms governing access to and use of the Sprntly platform.",
}

export default function TermsOfUsePage() {
  return (
    <LegalDocument
      title="Sprntly Terms of Use"
      effective={TERMS_EFFECTIVE}
      lastUpdated={TERMS_LAST_UPDATED}
      sections={TERMS_SECTIONS}
      contactEmail="legal@sprntly.ai"
      contactLabel="legal@sprntly.ai"
      sibling={{ href: "/privacy", label: "Privacy Policy" }}
      beforeSections={
        <p className="legal-p">
          By accessing or using the Service, you agree to these Terms and to
          our{" "}
          <Link href={publicPath("/privacy")}>Privacy Policy</Link>. If you do
          not agree, do not use the Service.
        </p>
      }
    />
  )
}
