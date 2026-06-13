#!/usr/bin/env python3
"""
sample_size.py — rough per-arm sample size for a two-proportion A/B test.
Uses normal approximation, power=0.8, alpha=0.05 (two-sided) by default.

Usage:
  python3 sample_size.py --baseline 0.05 --mde 0.005
  python3 sample_size.py --baseline 0.05 --mde 0.005 --power 0.8 --alpha 0.05
"""
import argparse, math

def z(p):  # inverse normal CDF (Acklam approximation, good enough for power calcs)
    a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
    b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
    c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
    dd=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]
    pl=0.02425
    if p<pl:
        q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((dd[0]*q+dd[1])*q+dd[2])*q+dd[3])*q+1)
    if p<=1-pl:
        q=p-0.5; r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q=math.sqrt(-2*math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((dd[0]*q+dd[1])*q+dd[2])*q+dd[3])*q+1)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--baseline",type=float,required=True)
    ap.add_argument("--mde",type=float,required=True,help="absolute lift, e.g. 0.005 for +0.5pp")
    ap.add_argument("--power",type=float,default=0.8)
    ap.add_argument("--alpha",type=float,default=0.05)
    a=ap.parse_args()
    p1=a.baseline; p2=a.baseline+a.mde
    za=z(1-a.alpha/2); zb=z(a.power)
    pbar=(p1+p2)/2
    n=((za*math.sqrt(2*pbar*(1-pbar))+zb*math.sqrt(p1*(1-p1)+p2*(1-p2)))**2)/(a.mde**2)
    print(f"Per-arm sample size ~ {math.ceil(n):,} (baseline {p1:.3f} -> {p2:.3f}, power {a.power}, alpha {a.alpha})")
    print(f"Total (2 arms) ~ {math.ceil(n)*2:,}")

if __name__=="__main__":
    main()
