import type { Metadata } from "next"
import Link from "next/link"
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
    <div className="legal-page">
      <header className="legal-header">
        <Link href="/" className="legal-brand">
          spr<span>ntly</span>
        </Link>
      </header>

      <main className="legal-main">
        <h1 className="legal-title">Sprntly Privacy Policy</h1>
        <p className="legal-meta">
          Effective date: {PRIVACY_EFFECTIVE}
          <br />
          Last updated: {PRIVACY_LAST_UPDATED}
        </p>

        {PRIVACY_SECTIONS.map((section) => (
          <section key={section.id} className="legal-section" id={section.id}>
            {section.title ? (
              <h2 className="legal-section-title">{section.title}</h2>
            ) : null}
            {section.blocks.map((paragraph, i) => (
              <p key={`${section.id}-${i}`} className="legal-p">
                {paragraph}
              </p>
            ))}
          </section>
        ))}
      </main>

      <footer className="legal-footer">
        <Link href="/">Back to Sprntly</Link>
        <span className="legal-footer-sep">·</span>
        <a href="mailto:privacy@sprntly.ai">privacy@sprntly.ai</a>
      </footer>
    </div>
  )
}
