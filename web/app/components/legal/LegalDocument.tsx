import Link from "next/link"
import { publicPath } from "../../lib/public-path"

export type LegalSection = {
  id: string
  title: string
  blocks: string[]
}

type Props = {
  title: string
  effective: string
  lastUpdated: string
  sections: LegalSection[]
  contactEmail: string
  contactLabel: string
  sibling?: { href: string; label: string }
  /** Optional content after the date line (e.g. Privacy Policy link in Terms intro). */
  beforeSections?: React.ReactNode
}

export function LegalDocument({
  title,
  effective,
  lastUpdated,
  sections,
  contactEmail,
  contactLabel,
  sibling,
  beforeSections,
}: Props) {
  return (
    <div className="legal-page">
      <header className="legal-header">
        <Link href={publicPath("/")} className="legal-brand">
          spr<span>ntly</span>
        </Link>
      </header>

      <main className="legal-main">
        <h1 className="legal-title">{title}</h1>
        <p className="legal-meta">
          Effective date: {effective}
          <br />
          Last updated: {lastUpdated}
        </p>

        {beforeSections}

        {sections.map((section) => (
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
        <Link href={publicPath("/")}>Back to Sprntly</Link>
        {sibling ? (
          <>
            <span className="legal-footer-sep">·</span>
            <Link href={publicPath(sibling.href)}>{sibling.label}</Link>
          </>
        ) : null}
        <span className="legal-footer-sep">·</span>
        <a href={`mailto:${contactEmail}`}>{contactLabel}</a>
      </footer>
    </div>
  )
}
