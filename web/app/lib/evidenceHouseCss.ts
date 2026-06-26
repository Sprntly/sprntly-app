/**
 * Canonical "house" stylesheet for the visual HTML evidence brief produced by
 * the `evidence-brief` skill. The model emits only the BODY HTML (house classes
 * + inline SVG charts); the app wraps it with this stylesheet inside a sandboxed
 * iframe (see `EvidenceHtmlFrame`). Keeping the CSS here — not in the generated
 * output — means every brief renders consistently and the look is maintainable
 * in one place. Mirrors the skill's `examples/*.html`.
 */
export const EVIDENCE_HOUSE_CSS = `
:root{
  --ink:#191b24; --paper:#fbfaf6; --muted:#6c7180; --hair:#e4e1d7;
  --problem:#dd4b32; --problem-soft:#f6d6cd;
  --opp:#0f9d77; --opp-soft:#d3efe5;
  --grid:#ece9e0; --bar-neutral:#cfccc2;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:-apple-system,system-ui,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:"SF Mono","Roboto Mono",ui-monospace,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:32px 28px 44px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin:0 0 14px}
h1{font-family:var(--serif);font-weight:600;font-size:32px;line-height:1.12;letter-spacing:-.01em;margin:0 0 8px}
.deck{font-family:var(--serif);font-size:18px;font-style:italic;color:#4a4d57;margin:0 0 4px;line-height:1.4}
.meta{font-family:var(--mono);font-size:11.5px;color:var(--muted);border-top:1px solid var(--hair);border-bottom:1px solid var(--hair);padding:9px 0;margin:18px 0 0}
.context{font-size:15px;color:#3a3d47;margin:22px 0 0}
.tldr{background:#fff;border:1px solid var(--hair);border-left:3px solid var(--ink);padding:18px 20px;margin:22px 0 0;font-size:15px}
.tldr h4{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 9px}
.tldr p{margin:0}
.opp-top{display:flex;gap:14px;align-items:flex-start;background:var(--opp-soft);border:1px solid #a9ddca;border-radius:5px;padding:15px 18px;margin:14px 0 0}
.opp-top .tag{font-family:var(--mono);font-size:10px;letter-spacing:.12em;color:var(--opp);background:#fff;border:1px solid #a9ddca;border-radius:3px;padding:4px 7px;white-space:nowrap;margin-top:2px}
.opp-top p{margin:0;font-size:15px;font-family:var(--serif)}
.opp-top b{color:#0b6f54}
section{margin:38px 0 0}
.kicker{font-family:var(--mono);font-size:12px;letter-spacing:.1em;margin:0 0 6px;color:var(--problem)}
.kicker.o{color:var(--opp)} .kicker.n{color:var(--muted)}
h2{font-family:var(--serif);font-weight:600;font-size:21px;line-height:1.22;margin:0 0 8px;letter-spacing:-.01em}
p{font-size:14.5px;margin:0 0 12px}
figure{margin:18px 0 4px;background:#fff;border:1px solid var(--hair);border-radius:4px;padding:18px 16px 12px}
figcaption{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:9px}
svg{width:100%;height:auto;display:block}
.ax{font-family:var(--mono);font-size:11px;fill:var(--muted)}
.vlabel{font-family:var(--mono);font-size:12px;fill:var(--ink);font-weight:600}
.blabel{font-family:var(--sans);font-size:12px;fill:var(--ink)}
.voc{display:grid;gap:12px;margin:18px 0 0}
.q{background:#fff;border:1px solid var(--hair);border-radius:4px;padding:14px 16px}
.q .ch{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;margin:0 0 6px}
.q .ch.rev{color:#c2410c}.q .ch.sup{color:var(--problem)}.q .ch.sale{color:#8a5a00}
.q p{font-family:var(--serif);font-style:italic;font-size:15px;margin:0;color:#33363f;line-height:1.4}
table{width:100%;border-collapse:collapse;margin:16px 0 0;font-size:13.5px}
th,td{text-align:center;padding:9px 6px;border-bottom:1px solid var(--hair)}
th{font-family:var(--mono);font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);font-weight:400}
td:first-child,th:first-child{text-align:left;font-size:13px}
.yes{color:var(--opp);font-weight:700}.no{color:var(--problem);font-weight:700}.us{font-weight:700}
.extract{background:var(--problem-soft);border-radius:4px;padding:13px 16px;margin:14px 0 0;font-size:14px}
.extract b{color:#a3331f}
.hyp{background:var(--ink);color:#f3f1ea;border-radius:5px;padding:26px;margin:16px 0 0}
.hyp h4{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#a9a395;margin:0 0 12px}
.hyp .stmt{font-family:var(--serif);font-size:19px;line-height:1.5;margin:0}
.hyp .stmt .b{color:#5ed3aa;font-weight:600}.hyp .stmt .v{color:#f3c98b;font-weight:600}.hyp .stmt .x{color:#f29d8b;font-weight:600}
.hyp .test{font-size:13px;color:#cfcabc;margin:16px 0 0;border-top:1px solid #34363f;padding-top:14px}
.hyp .test b{color:#f3f1ea}
.handoff{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;letter-spacing:.06em;color:#5ed3aa;margin:14px 0 0}
`.trim()

/**
 * True when an evidence payload is a visual HTML brief (skill v4+) rather than
 * the legacy `:::`-block markdown. The HTML body starts with an element — the
 * eyebrow `<p>` or a wrapping tag — so a leading `<` after trim is the signal.
 * `:::`-block and plain-markdown payloads never start with `<`.
 */
export function isHtmlEvidence(payload: string | null | undefined): boolean {
  if (!payload) return false
  return payload.trimStart().startsWith("<")
}
