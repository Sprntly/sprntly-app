#!/usr/bin/env python3
"""
saas_metrics.py — compute standard SaaS metrics from a JSON input.
Only computes what the inputs allow; reports what's missing.

Recognized input keys (all optional):
  signups, activation_rate, monthly_churn_rate, arpu (monthly),
  cac, new_mrr, churned_mrr, starting_mrr, sales_marketing_spend, net_new_arr

Usage: echo '{"arpu":40,"monthly_churn_rate":0.042,"cac":90,"activation_rate":0.38,"signups":5000}' | python3 saas_metrics.py -
"""
import sys, json

def main():
    raw = sys.stdin.read() if (len(sys.argv)<2 or sys.argv[1]=="-") else open(sys.argv[1]).read()
    d = json.loads(raw)
    out, missing = {}, []
    arpu = d.get("arpu"); churn = d.get("monthly_churn_rate"); cac = d.get("cac")
    if arpu and churn:
        ltv = arpu / churn
        out["avg_customer_lifetime_months"] = round(1/churn,1)
        out["LTV"] = round(ltv,2)
        if cac:
            out["LTV_CAC_ratio"] = round(ltv/cac,2)
            out["CAC_payback_months"] = round(cac/arpu,2)
    else:
        if not arpu: missing.append("arpu")
        if not churn: missing.append("monthly_churn_rate")
    if d.get("signups") and d.get("activation_rate"):
        out["activated_users"] = int(d["signups"]*d["activation_rate"])
    # Quick ratio = (new + expansion MRR) / (churned + contraction MRR); here simplified
    if d.get("new_mrr") and d.get("churned_mrr"):
        out["quick_ratio"] = round(d["new_mrr"]/max(d["churned_mrr"],1e-9),2)
    if d.get("net_new_arr") and d.get("sales_marketing_spend"):
        out["magic_number"] = round(d["net_new_arr"]/max(d["sales_marketing_spend"],1e-9),2)
    print("# SaaS metrics")
    for k,v in out.items(): print(f"  {k:<28} {v}")
    if missing: print(f"\n  (need for more: {', '.join(missing)})")
    if not out: print("  (insufficient inputs)")

if __name__ == "__main__":
    main()
