// Presentational v4 verify-email scene (page 04). No hooks.
import Link from "next/link"
import { AuthShell } from "./AuthShell"
import { CircleCheck, InfoCircle, MailCheck, Refresh } from "./icons"

export type VerifyEmailViewProps = {
  email: string
  message: string | null
  resendCooldown: number
  canResend: boolean
  onContinue: () => void
  onResend: () => void
}

export function VerifyEmailView(props: VerifyEmailViewProps) {
  return (
    <AuthShell tag="Verify email" cardClassName="auth-card-center">
      <div className="verify-icon">
        <MailCheck width={30} height={30} />
      </div>
      <div className="auth-h">Check your <em>inbox.</em></div>
      <div className="auth-sub">
        We sent a verification link to your work email. Click it to continue.
      </div>
      <div className="verify-email">{props.email || "your work email"}</div>
      {props.message && <div className="auth-msg">{props.message}</div>}
      <button type="button" className="btn btn-brand btn-block" onClick={props.onContinue}>
        <CircleCheck width={14} height={14} />
        I&apos;ve verified — continue
      </button>
      <button
        type="button"
        className="btn btn-ghost btn-block"
        style={{ marginTop: 8, fontSize: 12, padding: 9 }}
        onClick={props.onResend}
        disabled={!props.canResend}
      >
        <Refresh width={13} height={13} />
        {props.resendCooldown > 0 ? (
          <>
            Resend email <span className="muted">({props.resendCooldown}s)</span>
          </>
        ) : (
          "Resend email"
        )}
      </button>
      <div className="spam-note">
        <InfoCircle width={14} height={14} />
        <div>Check your spam folder if it doesn&apos;t arrive. Link expires in 24 hours.</div>
      </div>
      <div className="auth-foot">
        Wrong address? <Link href="/sign-up">Create a new account</Link> ·{" "}
        <Link href="/sign-in">Sign in</Link>
      </div>
    </AuthShell>
  )
}
