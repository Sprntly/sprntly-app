"use client"

/**
 * Internal transcript viewer — read customer chats to QA the AI's answers.
 *
 * Replaces the "email ourselves every morning" idea: the same transcripts,
 * but filterable by date and company and read in place, so conversation
 * content never lands in anyone's inbox.
 *
 * Standalone session, like the staff panel but a SEPARATE credential: no
 * transcripts token in sessionStorage ⇒ a minimal access-code form. POST
 * /v1/transcripts/login stores a short-lived JWT (sprntly_transcripts_token)
 * and every call sends it as the Bearer. Any 401/404 — expired token, disabled
 * surface, wrong code — clears the token and drops back to the form.
 *
 * The page's URL is deliberately obscure, but that is NOT the security
 * boundary: this is a static export, so its JS ships to every visitor. The
 * backend's access-code gate is what protects the data.
 */

import { useCallback, useEffect, useMemo, useState } from "react"

import { AskReplyBody } from "../../shared/AskReplyBody"
import {
  ApiError,
  transcriptsApi,
  transcriptsAuth,
  type AskResponse,
  type TranscriptDetail,
  type TranscriptSummary,
} from "../../../lib/api"

/**
 * Adapt a stored turn to the shape the chat renderer takes.
 *
 * Turns are persisted as the raw assistant payload (markdown, or a whole HTML
 * document for a generated artifact), while `AskReplyBody` — the SAME component
 * the chat thread uses for every assistant turn — takes an `AskResponse`. Only
 * `answer` is meaningful here: per-turn citations/key points aren't stored in
 * the transcript tables, and citations are suppressed at the call site anyway.
 */
function asReply(content: string): AskResponse {
  return { answer: content, key_points: [], citations: [], confidence: 0, unanswered: "" }
}

/** YYYY-MM-DD in UTC — the backend filters on UTC day boundaries, so building
 *  these from local time would silently shift the range near midnight. */
function utcDay(offsetDays = 0): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() + offsetDays)
  return d.toISOString().slice(0, 10)
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

// ── Login ──

function AccessCodeForm({ onSuccess }: { onSuccess: () => void }) {
  const [code, setCode] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await transcriptsAuth.login(code)
      onSuccess()
    } catch (e) {
      // 401 = wrong code; 404 = the surface is disabled — same stealth posture
      // as the staff panel, so neither reveals whether the box is configured.
      if (e instanceof ApiError && e.status === 401) {
        setError("Invalid access code.")
      } else if (e instanceof ApiError && e.status === 404) {
        setError("Not found.")
      } else {
        setError("Sign-in failed — try again.")
      }
      setSubmitting(false)
    }
  }

  return (
    <form
      className="tvw-login"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <h1>Transcript review</h1>
      <label className="tvw-field">
        <span className="tvw-field-label">Access code</span>
        <input
          type="password"
          autoComplete="current-password"
          autoFocus
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      {error && <p className="tvw-error">{error}</p>}
      <div className="tvw-actions">
        <button
          type="submit"
          className="tvw-btn primary"
          disabled={submitting || !code}
        >
          {submitting ? "Checking…" : "Enter"}
        </button>
      </div>
    </form>
  )
}

// ── Conversation drawer ──

function TranscriptDrawer({
  detail,
  loading,
  onClose,
}: {
  detail: TranscriptDetail | null
  loading: boolean
  onClose: () => void
}) {
  // Escape closes, matching the app's other drawers.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const conv = detail?.conversation
  // Old rows store the whole exchange in query/reply and have no turn rows;
  // synthesise a two-message thread so they render identically.
  const turns = useMemo(() => {
    if (!detail) return []
    if (detail.turns.length > 0) return detail.turns
    if (!conv?.query && !conv?.reply) return []
    return [
      { id: -1, role: "user" as const, content: conv?.query ?? "", created_at: null },
      {
        id: -2,
        role: "assistant" as const,
        content: conv?.reply ?? "",
        created_at: null,
      },
    ].filter((t) => t.content)
  }, [detail, conv])

  return (
    <>
      <div className="tvw-scrim" onClick={onClose} />
      <aside className="tvw-drawer" role="dialog" aria-label="Conversation">
        <header className="tvw-drawer-head">
          <div>
            <h2>{conv?.title || "Conversation"}</h2>
            {conv && (
              <p className="tvw-drawer-meta">
                {conv.company_name}
                {conv.user_label ? ` · ${conv.user_label}` : ""} ·{" "}
                {formatWhen(conv.created_at)}
              </p>
            )}
          </div>
          <button className="tvw-btn" onClick={onClose} aria-label="Close">
            Close
          </button>
        </header>

        <div className="tvw-thread">
          {loading && <p className="tvw-muted">Loading…</p>}
          {!loading && turns.length === 0 && (
            <p className="tvw-muted">This conversation has no messages.</p>
          )}
          {!loading &&
            turns.map((t) => (
              <div key={t.id} className={`tvw-turn ${t.role}`}>
                <span className="tvw-turn-role">
                  {t.role === "user" ? "User" : "AI"}
                </span>
                {t.role === "user" ? (
                  // What the member typed — verbatim, exactly as the chat's own
                  // user bubble shows it (no markdown pass over user input).
                  <div className="tvw-turn-body tvw-turn-body--plain">{t.content}</div>
                ) : (
                  // Assistant turns go through the chat renderer itself, so a
                  // transcript reads the way the customer saw it: markdown as
                  // prose, ```chart blocks as charts, and a full HTML document
                  // (PRDs and other generated artifacts) in HtmlReportView's
                  // SANDBOXED iframe rather than as printed source.
                  //
                  // Read-only by construction: AskReplyBody renders content and
                  // nothing else — no composer, regenerate, edit or open-in-canvas
                  // affordance exists inside it. `omitCitations` drops the source
                  // cards (there are no per-turn citations to show), and both
                  // animateIn/simulateTyping stay off so a QA read isn't gated on
                  // a typing animation replaying the answer.
                  <div className="tvw-turn-body">
                    <AskReplyBody reply={asReply(t.content)} omitCitations />
                  </div>
                )}
              </div>
            ))}
        </div>
      </aside>
    </>
  )
}

// ── Screen ──

type LoadState = "checking" | "login" | "loading" | "ready" | "error"

export function TranscriptsScreen() {
  const [state, setState] = useState<LoadState>("checking")
  const [rows, setRows] = useState<TranscriptSummary[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [companies, setCompanies] = useState<{ id: string; display_name: string }[]>(
    [],
  )

  // Default to the last week — a single day is often empty, which reads as a
  // broken page rather than a quiet one.
  const [dateFrom, setDateFrom] = useState(() => utcDay(-7))
  const [dateTo, setDateTo] = useState(() => utcDay(0))
  const [companyId, setCompanyId] = useState("")

  const [openId, setOpenId] = useState<number | null>(null)
  const [detail, setDetail] = useState<TranscriptDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  /** 401/404 ⇒ the token is missing/expired/rejected (or the surface is off):
   *  clear it and drop back to the code form. Everything else is a real error. */
  const handleAuthError = useCallback((e: unknown): boolean => {
    if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
      transcriptsAuth.logout()
      setState("login")
      return true
    }
    return false
  }, [])

  const load = useCallback(async () => {
    setState("loading")
    try {
      const [list, comps] = await Promise.all([
        transcriptsApi.listConversations({
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          company_id: companyId || undefined,
        }),
        transcriptsApi.listCompanies(),
      ])
      setRows(list.conversations)
      setHasMore(list.has_more)
      setCompanies(comps.companies)
      setState("ready")
    } catch (e) {
      if (!handleAuthError(e)) setState("error")
    }
  }, [dateFrom, dateTo, companyId, handleAuthError])

  useEffect(() => {
    // sessionStorage is browser-only — decide login-vs-load after mount so the
    // statically exported page hydrates cleanly.
    if (transcriptsAuth.hasToken()) {
      void load()
    } else {
      setState("login")
    }
    // Intentionally mount-only: filter changes reload via the explicit Apply
    // button, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openConversation = async (id: number) => {
    setOpenId(id)
    setDetail(null)
    setDetailLoading(true)
    try {
      setDetail(await transcriptsApi.getConversation(id))
    } catch (e) {
      if (!handleAuthError(e)) setOpenId(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const closeDrawer = () => {
    setOpenId(null)
    setDetail(null)
  }

  const signOut = () => {
    transcriptsAuth.logout()
    setRows([])
    setCompanies([])
    closeDrawer()
    setState("login")
  }

  if (state === "checking") {
    return (
      <div className="tvw-shell tvw-muted">
        Loading…
        <ScopedStyle />
      </div>
    )
  }

  if (state === "login") {
    return (
      <div className="tvw-shell">
        <AccessCodeForm onSuccess={() => void load()} />
        <ScopedStyle />
      </div>
    )
  }

  return (
    <div className="tvw-shell">
      <header className="tvw-head">
        <h1>Transcript review</h1>
        <button className="tvw-btn" onClick={signOut}>
          Sign out
        </button>
      </header>

      <form
        className="tvw-filters"
        onSubmit={(e) => {
          e.preventDefault()
          void load()
        }}
      >
        <label className="tvw-filter">
          <span>From</span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </label>
        <label className="tvw-filter">
          <span>To</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </label>
        <label className="tvw-filter">
          <span>Company</span>
          <select
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value)}
          >
            <option value="">All companies</option>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="tvw-btn primary">
          Apply
        </button>
      </form>

      {state === "error" && (
        <p className="tvw-error">Couldn’t load transcripts — try again.</p>
      )}

      {state === "loading" && <p className="tvw-muted">Loading…</p>}

      {state === "ready" && rows.length === 0 && (
        <p className="tvw-muted">No conversations in this range.</p>
      )}

      {state === "ready" && rows.length > 0 && (
        <>
          <div className="tvw-table-wrap">
            <table className="tvw-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Company</th>
                  <th>Member</th>
                  <th>Conversation</th>
                  <th>Messages</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    className={openId === r.id ? "is-open" : undefined}
                    onClick={() => void openConversation(r.id)}
                  >
                    <td className="tvw-nowrap">{formatWhen(r.created_at)}</td>
                    <td>{r.company_name}</td>
                    <td>{r.user_label ?? "—"}</td>
                    <td>
                      <span className="tvw-title">{r.title || "Untitled"}</span>
                      {r.preview && (
                        <span className="tvw-preview">{r.preview}</span>
                      )}
                    </td>
                    <td className="tvw-nowrap">{r.turn_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <p className="tvw-muted">
              Showing the newest 100 — narrow the date range to see more.
            </p>
          )}
        </>
      )}

      {openId !== null && (
        <TranscriptDrawer
          detail={detail}
          loading={detailLoading}
          onClose={closeDrawer}
        />
      )}
      <ScopedStyle />
    </div>
  )
}

// Scoped styles — this page stands alone (no app shell), so it carries its own
// CSS rather than relying on globals.css, mirroring StaffAdminScreen.
function ScopedStyle() {
  return (
    <style>{`
    .tvw-shell { max-width: 1100px; margin: 0 auto; padding: 40px 24px 80px;
      font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: #111; }
    .tvw-head { display: flex; align-items: center; justify-content: space-between;
      gap: 16px; margin-bottom: 20px; }
    .tvw-head h1 { font-size: 22px; font-weight: 600; margin: 0; }
    .tvw-muted { color: #888; font-size: 13px; }

    .tvw-login { max-width: 320px; margin: 96px auto 0; display: flex;
      flex-direction: column; gap: 14px; }
    .tvw-login h1 { font-size: 22px; font-weight: 600; margin: 0 0 6px; }
    .tvw-field { display: flex; flex-direction: column; gap: 4px; }
    .tvw-field input { font-size: 13px; padding: 7px 10px; border-radius: 7px;
      border: 1px solid #d8d8d8; }
    .tvw-field-label { font-size: 12px; font-weight: 600; }
    .tvw-actions { display: flex; gap: 8px; margin-top: 14px; }
    .tvw-error { color: #b42318; font-size: 13px; margin: 10px 0 0; }

    .tvw-btn { font-size: 12px; padding: 5px 12px; border-radius: 7px;
      border: 1px solid #d8d8d8; background: #fff; cursor: pointer; }
    .tvw-btn:hover { background: #f7f7f7; }
    .tvw-btn.primary { background: #111; color: #fff; border-color: #111; }
    .tvw-btn.primary:hover { background: #333; }
    .tvw-btn:disabled { opacity: 0.5; cursor: default; }

    .tvw-filters { display: flex; align-items: flex-end; gap: 12px;
      flex-wrap: wrap; margin-bottom: 18px; }
    .tvw-filter { display: flex; flex-direction: column; gap: 4px; }
    .tvw-filter > span { font-size: 12px; font-weight: 600; }
    .tvw-filter input, .tvw-filter select { font-size: 13px; padding: 6px 10px;
      border-radius: 7px; border: 1px solid #d8d8d8; background: #fff; }

    /* Wide content scrolls inside its own container, never the page body. */
    .tvw-table-wrap { overflow-x: auto; }
    .tvw-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .tvw-table th { text-align: left; font-size: 11px; font-weight: 600;
      text-transform: uppercase; letter-spacing: 0.04em; color: #888;
      padding: 0 12px 8px; border-bottom: 1px solid #e5e5e5; }
    .tvw-table td { padding: 10px 12px; border-bottom: 1px solid #f0f0f0;
      vertical-align: top; }
    .tvw-table tbody tr { cursor: pointer; }
    .tvw-table tbody tr:hover { background: #fafafa; }
    .tvw-table tbody tr.is-open { background: #f1f5ff; }
    .tvw-nowrap { white-space: nowrap; color: #555; }
    .tvw-title { display: block; font-weight: 600; }
    .tvw-preview { display: block; color: #777; margin-top: 2px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
      max-width: 420px; }

    .tvw-scrim { position: fixed; inset: 0; background: rgba(0,0,0,0.28);
      z-index: 40; }
    .tvw-drawer { position: fixed; top: 0; right: 0; bottom: 0;
      /* Wide enough that a rendered HTML artifact (PRD pages assume a document
         column) is readable without an inner horizontal scroll. */
      width: min(880px, 100vw); background: #fff; z-index: 41;
      border-left: 1px solid #e5e5e5; display: flex; flex-direction: column;
      box-shadow: -8px 0 24px rgba(0,0,0,0.08); }
    .tvw-drawer-head { display: flex; align-items: flex-start; gap: 16px;
      justify-content: space-between; padding: 20px 20px 14px;
      border-bottom: 1px solid #eee; }
    .tvw-drawer-head h2 { font-size: 16px; font-weight: 600; margin: 0; }
    .tvw-drawer-meta { font-size: 12px; color: #666; margin: 4px 0 0; }
    .tvw-thread { overflow-y: auto; padding: 18px 20px 40px; display: flex;
      flex-direction: column; gap: 16px; }
    .tvw-turn { display: flex; flex-direction: column; gap: 4px; }
    .tvw-turn-role { font-size: 11px; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.04em; color: #888; }
    .tvw-turn.assistant .tvw-turn-role { color: #137a3d; }
    .tvw-turn-body { font-size: 13px; line-height: 1.55;
      word-break: break-word; background: #f7f7f8; border-radius: 8px;
      padding: 10px 12px; }
    /* User turns only: keep the member's own line breaks. Assistant turns are
       rendered markdown/HTML (see AskReplyBody) and must NOT be pre-wrapped. */
    .tvw-turn-body--plain { white-space: pre-wrap; }
    .tvw-turn.assistant .tvw-turn-body { background: #f2f9f5; }
    /* A full-HTML artifact renders as a sandboxed iframe that sizes itself to
       its content — give it the full panel width, no tinted card behind it. */
    .tvw-turn.assistant .tvw-turn-body:has(> iframe) { background: none;
      padding: 0; }
  `}</style>
  )
}
