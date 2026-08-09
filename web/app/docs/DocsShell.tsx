"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import {
  IconChevronRight,
  IconMessage2,
} from "@tabler/icons-react"
import { publicPath } from "../lib/public-path"
import { DOCS, getDoc } from "./content"
import { DocsTopbar } from "./DocsTopbar"
import { DocMarkdown } from "./DocMarkdown"

/**
 * The document reader: left sidebar (all docs + current doc's sections),
 * the article, and a right "On this page" rail with scroll-spy. Content is
 * hardcoded and resolved from the registry by `slug`.
 */
export function DocsShell({ slug }: { slug: string }) {
  const doc = getDoc(slug)
  const [activeId, setActiveId] = useState<string>(doc?.sections[0]?.id ?? "")
  const contentRef = useRef<HTMLDivElement>(null)

  // Scroll-spy: highlight the section nearest the top of the viewport.
  useEffect(() => {
    if (!doc) return
    const headings = doc.sections
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => el !== null)
    if (headings.length === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveId(visible[0].target.id)
      },
      { rootMargin: "-96px 0px -70% 0px", threshold: 0 },
    )
    headings.forEach((h) => observer.observe(h))
    return () => observer.disconnect()
  }, [doc])

  // Deep-link: on mount, scroll to the hash anchor (offset for the sticky bar).
  useEffect(() => {
    if (!doc) return
    const hash = window.location.hash.slice(1)
    if (hash && document.getElementById(hash)) {
      requestAnimationFrame(() => {
        document.getElementById(hash)?.scrollIntoView({ behavior: "auto" })
      })
    }
  }, [doc])

  if (!doc) {
    return (
      <>
        <DocsTopbar />
        <main className="docs-notfound">
          <h1>Document not found</h1>
          <p>The document you’re looking for doesn’t exist or has moved.</p>
          <Link href={publicPath("/docs")} className="docs-btn">
            Back to all docs
          </Link>
        </main>
      </>
    )
  }

  function jumpTo(id: string) {
    setActiveId(id)
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" })
    history.replaceState(null, "", publicPath(`/docs/${doc!.slug}#${id}`))
  }

  return (
    <>
      <DocsTopbar />

      <div className="docs-layout">
        {/* Left nav — all documents + current doc's sections */}
        <aside className="docs-sidebar" aria-label="Documentation navigation">
          <nav className="docs-nav">
            <p className="docs-nav-label">Documents</p>
            <ul className="docs-nav-list">
              {DOCS.map((d) => {
                const current = d.slug === doc.slug
                return (
                  <li key={d.slug}>
                    <Link
                      href={publicPath(`/docs/${d.slug}`)}
                      className={`docs-nav-doc${current ? " is-current" : ""}`}
                    >
                      {d.title}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </nav>
        </aside>

        {/* Article */}
        <main className="docs-content" ref={contentRef}>
          <nav className="docs-breadcrumb" aria-label="Breadcrumb">
            <Link href={publicPath("/docs")}>Docs</Link>
            <IconChevronRight size={14} stroke={1.8} />
            <span>{doc.category}</span>
            <IconChevronRight size={14} stroke={1.8} />
            <span className="docs-breadcrumb-current">{doc.title}</span>
          </nav>

          <header className="docs-article-header">
            <h1 className="docs-article-title">{doc.title}</h1>
            <p className="docs-article-desc">{doc.description}</p>
            {(doc.version || doc.updated) && (
              <p className="docs-article-meta">
                {doc.version ? <span>Version {doc.version}</span> : null}
                {doc.version && doc.updated ? (
                  <span className="docs-meta-sep">·</span>
                ) : null}
                {doc.updated ? <span>Last updated {doc.updated}</span> : null}
              </p>
            )}
          </header>

          <article>
            {doc.sections.map((section) => (
              <section
                key={section.id}
                id={section.id}
                className="docs-section"
              >
                <h2 className="docs-section-title">
                  <a
                    href={publicPath(`/docs/${doc.slug}#${section.id}`)}
                    className="docs-section-anchor"
                    onClick={(e) => {
                      e.preventDefault()
                      jumpTo(section.id)
                    }}
                    aria-label={`Link to ${section.title}`}
                  >
                    #
                  </a>
                  {section.title}
                </h2>
                <DocMarkdown body={section.body} />
              </section>
            ))}
          </article>

          <footer className="docs-article-footer">
            <p>
              Need a hand? Use the feedback icon in the app, or call{" "}
              <a href="tel:+12018525211">(201) 852-5211</a>.
            </p>
            <a href={publicPath("/")} className="docs-footer-cta">
              <IconMessage2 size={16} stroke={1.8} />
              Open Sprntly
            </a>
          </footer>
        </main>

        {/* On this page */}
        <aside className="docs-toc" aria-label="On this page">
          <p className="docs-toc-label">On this page</p>
          <ul className="docs-toc-list">
            {doc.sections.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={`docs-toc-link${
                    activeId === s.id ? " is-active" : ""
                  }`}
                  onClick={() => jumpTo(s.id)}
                >
                  {s.title}
                </button>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </>
  )
}
