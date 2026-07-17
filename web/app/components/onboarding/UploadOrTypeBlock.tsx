"use client"

import { useRef, type ReactNode, type SVGProps } from "react"
import { Check, FileText } from "../auth/icons"

function PencilIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...props}
    >
      <path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z" />
    </svg>
  )
}

function PaperclipIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...props}
    >
      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
  )
}

/**
 * One "upload OR type" block for the v6 onboarding steps 6-7 (Strategy &
 * roadmap / How your team decides): a typed-document upload card with a
 * "Type instead" toggle that swaps the card for a textarea ("Upload instead"
 * swaps back). Typed text is controlled by the parent (persisted with the
 * step's Continue); uploads fire immediately via `onPickFile`.
 */
export function UploadOrTypeBlock({
  title,
  sub,
  tint = "var(--accent-ink)",
  uploading,
  uploaded,
  fileName,
  notice,
  onPickFile,
  typedOpen,
  onToggleTyped,
  typed,
  onTypedChange,
  typedPlaceholder,
  dataField,
}: {
  title: string
  sub: ReactNode
  /** Icon tint (globals.css semantic var). */
  tint?: string
  uploading: boolean
  uploaded: boolean
  fileName: string | null
  notice: string | null
  onPickFile: (file: File | null) => void
  /** True → the textarea is shown instead of the upload card. */
  typedOpen: boolean
  onToggleTyped: () => void
  typed: string
  onTypedChange: (v: string) => void
  typedPlaceholder: string
  dataField: string
}) {
  const fileRef = useRef<HTMLInputElement | null>(null)
  return (
    <div className="onb-up-card" data-field={dataField}>
      {typedOpen ? (
        <>
          <div className="field-l" style={{ marginBottom: 6 }}>
            {title} <span className="opt">— {sub}</span>
          </div>
          <textarea
            className="inp"
            rows={4}
            value={typed}
            onChange={(e) => onTypedChange(e.target.value)}
            maxLength={4000}
            placeholder={typedPlaceholder}
            aria-label={title}
          />
        </>
      ) : (
        <button
          type="button"
          className={`onb-up onb-up-wide ${uploaded ? "has-file" : ""}`}
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          data-uploaded={uploaded ? "true" : undefined}
        >
          <span className="onb-up-ic" style={{ color: tint }} aria-hidden>
            {uploaded ? (
              <Check style={{ width: 16, height: 16 }} />
            ) : (
              <FileText style={{ width: 16, height: 16 }} />
            )}
          </span>
          <span className="onb-up-b">
            <span className="onb-up-t">
              {uploading ? "Uploading…" : fileName ?? title}
            </span>
            <span className="onb-up-s">
              {uploaded ? "Added — we'll fold it into your context." : sub}
            </span>
          </span>
          <span className="onb-up-s" aria-hidden>
            Drag &amp; drop or browse
          </span>
        </button>
      )}
      <input
        ref={fileRef}
        type="file"
        style={{ display: "none" }}
        onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
        aria-label={`${title} file`}
      />
      <button type="button" className="onb-toggle-link" onClick={onToggleTyped}>
        {typedOpen ? (
          <>
            <PaperclipIcon /> Upload instead
          </>
        ) : (
          <>
            <PencilIcon /> Type instead
          </>
        )}
      </button>
      {notice && (
        <p className="onb-field-hint" role="status">
          {notice}
        </p>
      )}
    </div>
  )
}
