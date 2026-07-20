"""Sprntly DS Agent v5 prototype: Semantic Ingestion Layer + Primitive Battery.
BLIND: reads only the dataset directory; never touches ground truth."""
import pandas as pd
from . import flags as FLAGS_MOD
import numpy as np, json, os, glob, re
from scipy import stats
def _is_str(ser): 
    import pandas as pd
    return pd.api.types.is_string_dtype(ser) or pd.api.types.is_object_dtype(ser)
def _is_num(ser):
    import pandas as pd
    return pd.api.types.is_numeric_dtype(ser)


# ══════════ SEMANTIC INGESTION LAYER ══════════
TOOL_SIGNATURES={
 "mixpanel":["mixpanel_"], "amplitude":["amplitude_"], "posthog":["posthog_"],
 "statsig":["statsig_"], "ga4":["ga4_"], "segment":["segment_"], "heap":["heap_"],
 "optimizely":["optimizely_"], "pendo":["pendo_"], "mparticle":["mparticle_"]}
ID_ALIASES=["user_id","distinct_id","$distinct_id","user_pseudo_id","visitor_id","account_id","anonymous_id"]
EVENT_ALIASES=["event","event_type","event_name","event_key","feature"]
TS_ALIASES=["ts","time","event_time","timestamp","sent_at","event_timestamp","day"]
PROP_ALIASES=["props","properties","event_properties","event_params","custom_attributes"]

def detect_tool(path):
    files=[os.path.basename(f) for f in glob.glob(f"{path}/*.csv")]
    for tool,sigs in TOOL_SIGNATURES.items():
        if any(any(f.startswith(s) for s in sigs) for f in files): return tool
    return "unknown"

def _first(cols, aliases):
    for a in aliases:
        if a in cols: return a
    return None

def schema_gate(df, name, issues):
    """Schema contract gate: dtype fixes, join integrity, silent-coercion bans."""
    for c in df.columns:
        if _is_str(df[c]):
            vals=df[c].dropna().unique()[:50]
            sv={str(v).lower() for v in vals}
            if sv and sv<= {"true","false","0","1","yes","no"}:
                df[c]=df[c].astype(str).str.lower().isin(["true","1","yes"]); issues.append(f"{name}.{c}: coerced to bool (was object)")
    # boolean columns stored as object -> real bool (RT3 bug class)
    return df

def _norm_col(c): return re.sub(r"[^a-z0-9]","",str(c).lower())

def _robust_read(f, issues, manifest):
    """v5.3 (R2): structural format hardening — banner-row / multi-row-header detection,
    quarantine-not-crash. Vendor-agnostic: works on field-count structure, not names."""
    base=os.path.basename(f)
    try:
        with open(f,"r",encoding="utf-8",errors="replace") as fh:
            head=[fh.readline() for _ in range(6)]
        counts=[sum(1 for x in l.rstrip("\n").split(",") if x.strip()) for l in head if l]
        if len(counts)>=3:
            mode=max(set(counts[1:]),key=counts[1:].count)
            skip=0
            for c in counts:
                if c<=max(2,int(mode*0.4)) and mode>=4: skip+=1
                else: break
            if skip>0:
                df=pd.read_csv(f,skiprows=skip,low_memory=False)
                issues.append(f"{base}: banner/preamble detected — skipped {skip} row(s) before header")
                manifest.append(dict(file=base,status="ingested",rows=len(df),note=f"header repaired (skipped {skip} banner row(s))"))
                return df
        df=pd.read_csv(f,low_memory=False)
        manifest.append(dict(file=base,status="ingested",rows=len(df),note=""))
        return df
    except Exception as e:
        issues.append(f"{base}: QUARANTINED — unparseable ({type(e).__name__}: {str(e)[:80]})")
        manifest.append(dict(file=base,status="quarantined",rows=0,note=f"unparseable: {type(e).__name__}"))
        return None

def load_canonical(path):
    """Return canonical: users(df), events(df: user_id,event,ts,props), monthly(dict), exposures(df), issues, tool.
    v5.3 additions: long_tables (id+date+measures grain), manifest, derived-entity fallback."""
    tool=detect_tool(path); issues=[]; users=None; events=None; monthly={}; exposures=[]
    long_tables={}; manifest=[]
    for f in sorted(glob.glob(f"{path}/*.csv")):
        base=os.path.basename(f); df=_robust_read(f,issues,manifest)
        if df is None: continue
        df=schema_gate(df,base,issues)
        cols=set(df.columns)
        ec=_first(cols,EVENT_ALIASES); ic=_first(cols,ID_ALIASES); tc=_first(cols,TS_ALIASES)
        # v5.3 (R2): long-table classification — id-like + date-like + numeric measures, no event col.
        # Placed BEFORE entity fallback; analytics-standard files (user_id etc.) never reach here changed.
        if ec is None and ic is None and len(df)>200:
            ncols={_norm_col(c):c for c in df.columns}
            datec=next((ncols[k] for k in ncols if k in ("day","date","dt","reportdate")),None)
            idcands=[(c,df[c].nunique()) for k,c in ncols.items() if k.endswith("id") and _is_str(df[c]) and df[c].nunique()>20]
            meas=[c for c in df.columns if _is_num(df[c]) and _norm_col(c) not in ("day","date")]
            if not idcands or datec is None:
                # v5.6 SIL vendor adapters (flag checked by caller via module attr — set in run()):
                # vendor-dialect dictionaries recover roles generic heuristics miss.
                if globals().get("_SIL_ADAPTERS_ON"):
                    from . import capabilities as CAP
                    vend, roles = CAP.vendor_roles(df.columns)
                    if vend:
                        vid=[c for c,r in roles.items() if r=="id" and df[c].nunique()>20]
                        vdt=[c for c,r in roles.items() if r=="date"]
                        if vid and not idcands: idcands=[(vid[0],df[vid[0]].nunique())]
                        if vdt and datec is None: datec=vdt[0]
                        if vid or vdt:
                            issues.append(f"{base}: vendor dialect '{vend}' recognized — roles recovered via SIL adapter")
            if idcands and meas:
                idc=max(idcands,key=lambda x:x[1])[0]
                long_tables[base]=dict(df=df,id=idc,date=datec,measures=meas)
                kind="LONG TABLE" if datec else "SUMMARY TABLE (no date grain)"
                manifest[-1]["status"]=f"ingested as {'long' if datec else 'summary'} table"
                manifest[-1]["note"]=(manifest[-1]["note"]+f"; grain: {idc}{' x '+datec if datec else ''}, {len(meas)} measures").strip("; ")
                issues.append(f"{base}: classified as {kind} (id={idc}, {len(meas)} numeric measures)")
                continue
        if "exposures" in base or {"experiment","group"}<=cols or {"experiment_key","variation_key"}<=cols:
            e=df.rename(columns={"experiment_key":"experiment","variation_key":"group", ic:"user_id"})
            exposures.append(e[["user_id","experiment","group"]]); continue
        if "month" in cols and ic:
            monthly[base]=df.rename(columns={ic:"account_id"}); continue
        if "date" in cols and ic is None and ec is None:
            monthly[base]=df; continue
        if ec and ic and tc:
            e=df.rename(columns={ic:"user_id",ec:"event",tc:"ts"})
            pc=_first(set(e.columns),PROP_ALIASES)
            if pc and pc!="props": e=e.rename(columns={pc:"props"})
            if "props" not in e: e["props"]="{}"
            # timestamp normalization: unix s / unix us / iso
            if _is_num(e["ts"]):
                mx=e["ts"].max()
                unit = "us" if mx>1e14 else ("ms" if mx>1e12 else "s")
                e["ts"]=pd.to_datetime(e["ts"],unit=unit); issues.append(f"{base}: unix ts normalized ({unit})")
            else: e["ts"]=pd.to_datetime(e["ts"])
            events = e[["user_id","event","ts","props"]] if events is None else pd.concat([events,e[["user_id","event","ts","props"]]])
            continue
        if ic:  # entity table
            u=df.rename(columns={ic:"user_id"})
            # expand JSON property columns
            for c in list(u.columns):
                if _is_str(u[c]) and u[c].dropna().astype(str).str.startswith("{").all() and len(u[c].dropna()):
                    try:
                        exp=pd.json_normalize(u[c].dropna().apply(json.loads))
                        exp.index=u[c].dropna().index
                        for nc in exp.columns:
                            if nc not in u: u[nc]=exp[nc]
                        u=u.drop(columns=[c]); issues.append(f"{base}.{c}: JSON expanded")
                    except Exception: pass
            users = u if users is None else users.merge(u,on="user_id",how="outer",suffixes=("","_dup"))
    if users is not None:
        ren={c:c[6:] for c in users.columns if c.startswith("trait_")}
        ren.update({c:c.lstrip("$") for c in users.columns if c.startswith("$")})
        if ren: users=users.rename(columns=ren); issues.append(f"vendor prefixes stripped: {len(ren)} cols")
    # v5.3 (R2): derived-entity fallback — if no entity table but long tables exist,
    # aggregate the largest long table per entity so entity-grain scans have a substrate.
    derived_entity=False
    if users is None and long_tables:
        base,lt=max(long_tables.items(),key=lambda kv:len(kv[1]["df"]))
        d=lt["df"]; agg=d.groupby(lt["id"])[lt["measures"]].sum().reset_index().rename(columns={lt["id"]:"user_id"})
        agg["_row_count"]=d.groupby(lt["id"]).size().values
        users=agg; derived_entity=True
        issues.append(f"entity table DERIVED by aggregating {base} per {lt['id']} (sums of {len(lt['measures'])} measures) — boolean practice scans unavailable at this grain")
    if users is None: issues.append("NO ENTITY TABLE FOUND")
    if users is not None and users.user_id.duplicated().any():
        issues.append("entity table: duplicate ids deduped"); users=users.drop_duplicates("user_id")
    # sampling detection heuristic: event volume per user per type << expected? (flag only)
    exposures=pd.concat(exposures) if exposures else None
    # detect goal metrics on entity grain (bool/binary cols with outcome-ish names or low-cardinality bool)
    goal_cols=[]
    if users is not None:
        for c in users.columns:
            if pd.api.types.is_bool_dtype(users[c]) and re.search(r"(retain|churn|convert|activ|upgrad|fail|purchas|commit|trial|renew|subscri|paid|book)",c,re.I):
                goal_cols.append(c)
    # v5.3 (R3): continuous goal detection — numeric outcome-named columns on the entity grain.
    # Additive: boolean goal list and its scans are untouched.
    cont_goals=[]
    if users is not None:
        for c in users.columns:
            if c=="user_id" or c in goal_cols: continue
            if _is_num(users[c]) and not pd.api.types.is_bool_dtype(users[c]) and re.search(r"(revenue|rev\b|earn|payout|amount|spend|price|ltv|mrr|arr)",c,re.I):
                cont_goals.append(c)
    return dict(tool=tool,users=users,events=events,monthly=monthly,exposures=exposures,
                issues=issues,goal_cols=goal_cols,cont_goals=cont_goals,
                long_tables=long_tables,manifest=manifest,derived_entity=derived_entity)

# ══════════ PRIMITIVE BATTERY ══════════
def T(p): return "MEASURED" if p<0.001 else ("INFERRED" if p<0.01 else "HYPOTHESIS")

def prop_gap(a_succ,a_n,b_succ,b_n):
    if min(a_n,b_n)<30: return None
    p1,p2=a_succ/a_n,b_succ/b_n
    z=stats.norm.sf(abs((p1-p2)/np.sqrt(p1*(1-p1)/a_n+p2*(1-p2)/b_n+1e-12)))*2
    return p1,p2,z

def battery(can):
    F=[]; u=can["users"]; ev=can["events"]; monthly=can["monthly"]; goals=can["goal_cols"]
    bool_feats=[c for c in u.columns if pd.api.types.is_bool_dtype(u[c]) and c not in goals] if u is not None else []
    cat_feats=[c for c in u.columns if _is_str(u[c]) and c!="user_id" and u[c].nunique()<=12
               and not (set(map(str,u[c].dropna().unique()))<={"control","treatment"} or c.endswith("_group"))] if u is not None else []
    def add(typ,claim,tier,cohort,stats_d,**kw):
        F.append(dict(type=typ,claim=claim,evidence=tier,cohort_code=cohort,stats=stats_d,**kw))

    # P1: flag vs goal scan (+ null results for red-herring reporting)
    for g in goals:
        for f in bool_feats:
            r=prop_gap(u[u[f]][g].sum(),u[f].sum(),u[~u[f]][g].sum(),(~u[f]).sum())
            if not r: continue
            p1,p2,pv=r; gap=p1-p2
            if abs(gap)>=0.10 and pv<0.01:
                add("flag_vs_goal",f"{f}=True → {g} {p1:.0%} vs {p2:.0%}",T(pv),f"df[df['{f}']]",
                    dict(gap=round(gap,3),p=pv),feature=f,goal=g,direction="pos" if gap>0 else "neg")
            elif pv>0.05:
                add("null_result",f"{f} shows no effect on {g} (Δ={gap:+.1%}, p={pv:.2f})","MEASURED",
                    f"df[df['{f}']]",dict(gap=round(gap,3),p=pv),feature=f,goal=g)

    # P2: categorical concentration (rates of bool goals by category)
    for g in goals:
        base=u[g].mean()
        for d in cat_feats:
            rates=u.groupby(d)[g].agg(["mean","count"])
            for val,row in rates.iterrows():
                if row["count"]>=60 and base>0 and row["mean"]/max(base,1e-9)>=2.0 and row["mean"]-base>=0.08:
                    others=u[u[d]!=val]
                    r=prop_gap(u[u[d]==val][g].sum(),row["count"],others[g].sum(),len(others))
                    if r and r[2]<0.01:
                        add("cat_concentration",f"{d}={val}: {g} {row['mean']:.0%} vs {r[1]:.0%} elsewhere",
                            T(r[2]),f"df[df['{d}']=='{val}']",dict(ratio=round(row['mean']/base,2),p=r[2]),
                            dimension=d,value=str(val),goal=g)

    # P3: n-way offset scan with RANKING/DEDUP layer — top candidate per goal, MEASURED-only
    import itertools
    REGION_MAP={"US":"NA","Canada":"NA","UK":"EU","Germany":"EU","France":"EU","Brazil":"LATAM","India":"APAC","Japan":"APAC"}
    derived=[]
    if u is not None:
        for c in list(cat_feats):
            vals=set(u[c].dropna().unique())
            if vals and vals<=set(REGION_MAP):
                u[c+"_region"]=u[c].map(REGION_MAP); derived.append(c+"_region")
            if "free" in {str(v).lower() for v in vals} and len(vals)>2:
                u[c+"_group"]=np.where(u[c].astype(str).str.lower()=="free","free","paid"); derived.append(c+"_group")
    for g in goals:
        base=u[g].mean(); cands=[]
        dims=(cat_feats+derived)[:9]
        for combo in list(itertools.combinations(dims,2))+list(itertools.combinations(dims,3)):
            grp=u.groupby(list(combo))[g].agg(["mean","count"])
            grp=grp[grp["count"]>=50]
            if len(grp)<2: continue
            lo=grp["mean"].idxmin(); hi=grp["mean"].idxmax()
            spread=grp["mean"].max()-grp["mean"].min()
            marg=u.groupby(combo[0])[g].mean()
            if spread>=0.15 and (marg.max()-marg.min())<spread*0.5:
                cell=lo if isinstance(lo,tuple) else (lo,)
                nlo=int(grp.loc[lo,"count"]); nhi=int(grp.loc[hi,"count"])
                r=prop_gap(int(round(grp.loc[lo,'mean']*nlo)),nlo,int(round(grp.loc[hi,'mean']*nhi)),nhi)
                pv=r[2] if r else 1
                if pv<0.01:
                    cands.append((-pv,combo,cell,grp.loc[lo,'mean'],grp.loc[hi,'mean'],spread,pv))
        # dedup: drop 3-way if a 2-way subset of it already fires; keep top-1
        cands.sort(reverse=True)
        kept=[]
        for c in cands:
            if any(set(k[1])<set(c[1]) for k in kept): continue
            kept.append(c)
            if len(kept)>=2: break
        for _,combo,cell,lom,him,spread,pv in kept:
            cond=" & ".join([f"(df['{d}']=='{v}')" for d,v in zip(combo,cell)])
            add("nway_offset",f"{'×'.join(combo)}: cell {cell} at {lom:.0%} vs {him:.0%} best — marginal looks flat (offsetting groups)",
                T(pv),cond,dict(spread=round(spread,3),p=pv),dims=list(combo),
                low_cell=[str(v) for v in cell],goal=g)

    # P4: Simpson decomposition scan — every flag×goal re-run within each cat dim
    for g in goals:
        for f in bool_feats:
            agg=prop_gap(u[u[f]][g].sum(),u[f].sum(),u[~u[f]][g].sum(),(~u[f]).sum())
            for d in cat_feats:
                for val in u[d].dropna().unique():
                    s=u[u[d]==val]
                    r=prop_gap(s[s[f]][g].sum(),s[f].sum(),s[~s[f]][g].sum(),(~s[f]).sum())
                    if r and r[2]<0.001 and abs(r[0]-r[1])>=0.15 and (not agg or abs(r[0]-r[1])>2.2*abs(agg[0]-agg[1])):
                        add("simpson",f"{f}→{g} within {d}={val}: {r[0]:.0%} vs {r[1]:.0%} (aggregate masks it: {agg[0]:.0%} vs {agg[1]:.0%})",
                            T(r[2]),f"df[(df['{d}']=='{val}') & df['{f}']]",dict(seg_gap=round(r[0]-r[1],3),agg_gap=round((agg[0]-agg[1]) if agg else 0,3),p=r[2]),
                            feature=f,segment_dim=d,segment=str(val),goal=g)

    # P5: pairwise flag interaction on goals
    for g in goals:
        for i,f1 in enumerate(bool_feats):
            for f2 in bool_feats[i+1:]:
                both=u[f1]&u[f2]
                r_b=prop_gap(u[both][g].sum(),both.sum(),u[~both][g].sum(),(~both).sum())
                if not r_b: continue
                g1=abs(u[u[f1]][g].mean()-u[~u[f1]][g].mean()); g2=abs(u[u[f2]][g].mean()-u[~u[f2]][g].mean())
                gap=r_b[0]-r_b[1]
                if r_b[2]<0.001 and gap>=0.12 and gap>1.6*max(g1,g2):
                    add("flag_interaction",f"{f1} AND {f2} → {g} {r_b[0]:.0%} vs {r_b[1]:.0%}; neither alone explains it",
                        T(r_b[2]),f"df[df['{f1}'] & df['{f2}']]",dict(gap=round(gap,3),solo=[round(g1,3),round(g2,3)],p=r_b[2]),
                        features=sorted([f1,f2]),goal=g)

    # P6: funnel
    if ev is not None and len(ev):
        counts=ev.groupby("event").user_id.nunique().sort_values(ascending=False)
        onboard=[e for e in counts.index if re.search(r"(signup|create|invite|connect|first|report|complete)",e)]
        if len(onboard)>=3:
            seq=ev[ev.event.isin(onboard)].groupby("event").agg(n=("user_id","nunique"),t=("ts","median")).sort_values("n",ascending=False)
            prev=None
            for e,row in seq.iterrows():
                if prev is not None and prev>0:
                    conv=row.n/prev
                    if conv<0.4:
                        add("funnel_drop",f"step '{e}': only {conv:.0%} of users from prior step ({prev}→{row.n})",
                            "MEASURED",f"events[events.event=='{e}']",dict(conv=round(conv,3)),step=e)
                prev=row.n

    # P7: early-action threshold scan
    if ev is not None and u is not None and "signup_date" in u:
        sd=u.set_index("user_id").signup_date
        for g in goals:
            for act in ev.event.value_counts().head(8).index:
                sub=ev[ev.event==act].merge(sd.rename("sd"),left_on="user_id",right_index=True)
                sub=sub[(pd.to_datetime(sub.ts)-pd.to_datetime(sub.sd)).dt.days<=7]
                cnt=sub.groupby("user_id").size().reindex(u.user_id).fillna(0)
                best=None
                for k in [1,2,3,4,5]:
                    m=(cnt>=k).values
                    r=prop_gap(u[m][g].sum(),m.sum(),u[~m][g].sum(),(~m).sum())
                    if r and r[2]<0.001 and r[0]-r[1]>=0.15 and min(m.sum(),(~m).sum())>100:
                        if best is None or r[0]-r[1]>best[1]: best=(k,r[0]-r[1],r)
                if best:
                    k,gp,r=best
                    add("early_action_threshold",f"≥{k}× '{act}' in first 7d → {g} {r[0]:.0%} vs {r[1]:.0%}",
                        T(r[2]),f"first7d_count['{act}']>={k}",dict(gap=round(gp,3),k=k,p=r[2]),action=act,k=k,goal=g)

    # P8: sequence-order scan (first-timestamp ordering of top event pairs vs goals)
    if ev is not None and u is not None:
        firsts={e:ev[ev.event==e].groupby("user_id").ts.min() for e in ev.event.value_counts().head(6).index}
        keys=list(firsts)
        for g in goals:
            for i,e1 in enumerate(keys):
                for e2 in keys[i+1:]:
                    common=firsts[e1].index.intersection(firsts[e2].index)
                    if len(common)<200: continue
                    order=(firsts[e1].loc[common]<firsts[e2].loc[common])
                    gm=u.set_index("user_id")[g].reindex(common)
                    r=prop_gap(gm[order].sum(),order.sum(),gm[~order].sum(),(~order).sum())
                    if r and r[2]<0.001 and abs(r[0]-r[1])>=0.12:
                        a,b=(e1,e2) if r[0]>r[1] else (e2,e1)
                        add("sequence_order",f"'{a}' BEFORE '{b}' → {g} {max(r[0],r[1]):.0%} vs {min(r[0],r[1]):.0%} (order, not presence)",
                            T(r[2]),f"first_ts['{a}'] < first_ts['{b}']",dict(gap=round(abs(r[0]-r[1]),3),p=r[2]),first=a,second=b,goal=g)

    # P9: error-sequence cohort → metric growth
    if ev is not None and monthly:
        errs=[e for e in ev.event.unique() if re.search(r"error",e)]
        abandons=[e for e in ev.event.unique() if re.search(r"abandon",e)]
        for tbl in monthly.values():
            metric_cols=[c for c in tbl.columns if _is_num(tbl[c]) and c not in ("account_id",) ]
            if "month" not in tbl or "account_id" not in tbl: continue
            for errev in errs:
                ne=ev[ev.event==errev].groupby("user_id").size()
                ab=set(ev[ev.event.isin(abandons)].user_id) if abandons else set()
                cohort=[uid for uid in ne[ne>=4].index if uid in ab]
                if len(cohort)<20: continue
                for mc in metric_cols:
                    t=tbl.sort_values("month")
                    half=len(t.month.unique())//2
                    early=t[t.month.isin(sorted(t.month.unique())[:half])].groupby("account_id")[mc].sum()
                    late=t[t.month.isin(sorted(t.month.unique())[half:])].groupby("account_id")[mc].sum()
                    gc=(late.reindex(cohort).sum()/max(early.reindex(cohort).sum(),1e-9))
                    rest=[a for a in early.index if a not in set(cohort)]
                    gr=(late.reindex(rest).sum()/max(early.reindex(rest).sum(),1e-9))
                    if gr-gc>=0.08:
                        add("error_sequence_cohort",
                            f"{len(cohort)} accounts with ≥4 '{errev}' + abandonment: {mc} growth {gc:.2f}x vs {gr:.2f}x rest (pooled)",
                            "MEASURED",f"n_events['{errev}']>=4 & abandoned",
                            dict(cohort_growth=round(gc,3),rest_growth=round(gr,3),n=len(cohort),weighting="pooled"),
                            error=errev,metric=mc)

    # P10: interaction × intra-quarter seasonality
    if monthly and u is not None and len(bool_feats)>=2:
        for tbl in monthly.values():
            if "month" not in tbl or "account_id" not in tbl: continue
            mcols=[c for c in tbl.columns if _is_num(tbl[c]) and c!="account_id"]
            t=tbl.copy(); t["moq"]=((pd.PeriodIndex(t.month,freq="M").month-1)%3)+1
            for i,f1 in enumerate(bool_feats):
                for f2 in bool_feats:
                    if f1==f2: continue
                    coh=set(u[u[f1]&~u[f2]].user_id)
                    if len(coh)<40: continue
                    sub=t[t.account_id.isin(coh)]; ctr=t[~t.account_id.isin(coh)]
                    for mc in mcols:
                        s=sub.groupby("moq")[mc].mean(); c=ctr.groupby("moq")[mc].mean()
                        if len(s)<3: continue
                        dip=s[3]/s[[1,2]].mean(); cd=c[3]/c[[1,2]].mean()
                        if cd-dip>=0.08 and dip<0.93:
                            add("interaction_seasonality",
                                f"{f1}=True & {f2}=False ({len(coh)} accts): {mc} drops to {dip:.2f}x in month 3 of quarter (control {cd:.2f}x)",
                                "MEASURED",f"df['{f1}'] & ~df['{f2}']",dict(dip=round(dip,3),control=round(cd,3)),
                                flags=sorted([f1,f2]),metric=mc,dip_month=3)

    # P11: component divergence + pre-churn component decomposition
    if monthly:
        for tbl in monthly.values():
            if "month" not in tbl or "account_id" not in tbl: continue
            mcols=[c for c in tbl.columns if _is_num(tbl[c]) and c!="account_id"]
            if len(mcols)>=2:
                t=tbl.sort_values("month"); mos=sorted(t.month.unique()); half=len(mos)//2
                trends={c:(t[t.month.isin(mos[half:])][c].sum()/max(t[t.month.isin(mos[:half])][c].sum(),1e-9)) for c in mcols}
                dec=[c for c,v in trends.items() if v<0.95]; gro=[c for c,v in trends.items() if v>1.05]
                # per-account decomposition: share of accounts with decaying component
                for dc in mcols:
                    e=t[t.month.isin(mos[:half])].groupby("account_id")[dc].sum()
                    l=t[t.month.isin(mos[half:])].groupby("account_id")[dc].sum()
                    ratio=(l/e.replace(0,np.nan)).dropna()
                    leak=(ratio<0.75).mean()
                    others=[c for c in mcols if c!=dc]
                    if leak>=0.15 and others and trends[others[0]]>1.0:
                        add("component_divergence",
                            f"{leak:.0%} of accounts show '{dc}' decaying <0.75x while '{others[0]}' grows ({trends[others[0]]:.2f}x) — total masks workload leakage",
                            "MEASURED",f"ratio['{dc}']<0.75",dict(leak_share=round(leak,3),trends={k:round(v,2) for k,v in trends.items()}),
                            decaying=dc,growing=others[0])
            # pre-churn: needs churn info on entity
            if u is not None and "churned" in u.columns and "churn_month" in u.columns and len(mcols)>=2:
                chu=u[u.churned][["user_id","churn_month"]]
                mm=tbl.merge(chu,left_on="account_id",right_on="user_id")
                if len(mm):
                    mm["mb"]=(pd.PeriodIndex(mm.churn_month,freq="M")-pd.PeriodIndex(mm.month,freq="M")).map(lambda x:x.n)
                    w=mm[mm.mb.between(0,2)]; b=mm[mm.mb.between(3,5)]
                    for mc in mcols:
                        r=(w.groupby("account_id")[mc].mean()/b.groupby("account_id")[mc].mean().replace(0,np.nan)).mean()
                        oth=[c for c in mcols if c!=mc][0]
                        rt=(w.groupby("account_id")[oth].mean()/b.groupby("account_id")[oth].mean().replace(0,np.nan)).mean()
                        if r<0.65 and rt>0.8:
                            add("component_prechurn",
                                f"churned accounts: '{mc}' collapses to {r:.2f}x in final 3 months while '{oth}' holds at {rt:.2f}x — role-level early warning",
                                "MEASURED",f"months_before_churn<=2",dict(component_ratio=round(r,2),total_ratio=round(rt,2)),
                                component=mc,total=oth)

    # P12: DOW seasonality on daily tables
    for key,tbl in monthly.items():
        if "date" in tbl.columns:
            t=tbl.copy(); t["dow"]=pd.to_datetime(t.date).dt.dayofweek
            for mc in [c for c in t.columns if _is_num(t[c]) and c!="dow"]:
                wk=t[t.dow<5][mc].mean(); we=t[t.dow>=5][mc].mean()
                # v5.3 (R6): directional-symmetry fix — the scan previously fired only for
                # weekday-high patterns; weekend-high metrics were invisible.
                if we>0 and wk/we>=1.6:
                    add("seasonality_dow",f"'{mc}' runs {wk/we:.1f}x higher on weekdays vs weekends","MEASURED",
                        "dayofweek<5",dict(ratio=round(wk/we,2)),metric=mc)
                elif wk>0 and we/wk>=1.6:
                    add("seasonality_dow",f"'{mc}' runs {we/wk:.1f}x higher on weekends vs weekdays","MEASURED",
                        "dayofweek>=5",dict(ratio=round(we/wk,2)),metric=mc)

    # P13: experiment suite — SRM, lift+CI, HTE, novelty
    if can["exposures"] is not None and u is not None:
        ex=can["exposures"]
        for name,grp in ex.groupby("experiment"):
            counts=grp.group.value_counts()
            chi=stats.chisquare(counts.values)
            srm=chi.pvalue<0.001 and len(counts)==2
            goal=next((g for g in u.columns if pd.api.types.is_bool_dtype(u[g]) and name.split("_")[0] in g or g==f"{name}_converted"),None)
            if goal is None:
                cand=[g for g in goals if name in g]; goal=cand[0] if cand else (goals[0] if goals else None)
            if goal is None: continue
            uu=u.merge(grp,on="user_id")
            tmask=uu.group.isin(["treatment"]) if "treatment" in set(uu.group) else uu.group==sorted(uu.group.unique())[-1]
            r=prop_gap(uu[tmask][goal].sum(),tmask.sum(),uu[~tmask][goal].sum(),(~tmask).sum())
            lift=r[0]-r[1] if r else 0
            if srm:
                add("experiment_srm",f"{name}: SAMPLE RATIO MISMATCH ({counts.to_dict()}, χ² p={chi.pvalue:.1e}) — readout INVALID; observed +{lift:.1%} 'lift' cannot be trusted",
                    "MEASURED",f"exposures['{name}']",dict(srm_p=float(chi.pvalue),lift=round(lift,3)),name=name,verdict="invalid_srm",goal=goal)
                continue
            verdict="ship" if (r and r[2]<0.01 and lift>0.03) else "no_effect"
            # HTE scan
            hte=None
            if "signup_date" in u:
                newu=(pd.Timestamp("2026-03-01")-pd.to_datetime(uu.signup_date)).dt.days<120
                segs={}
                for segname,m in [("new_users",newu.values),("existing",~newu.values)]:
                    a=uu[m&tmask.values][goal]; b=uu[m&~tmask.values][goal]
                    if min(len(a),len(b))<50: continue
                    l=a.mean()-b.mean(); se=np.sqrt(a.mean()*(1-a.mean())/len(a)+b.mean()*(1-b.mean())/len(b))
                    segs[segname]=(l,se)
                if len(segs)==2:
                    (n1,(l1,s1)),(n2,(l2,s2))=segs.items()
                    zdiff=abs(l1-l2)/np.sqrt(s1**2+s2**2+1e-12); pdiff=stats.norm.sf(zdiff)*2
                    if pdiff<0.01 and max(abs(l1),abs(l2))>2*min(abs(l1),abs(l2))+0.01:
                        hot=n1 if abs(l1)>abs(l2) else n2
                        hte=(hot,segs[hot][0])
            # novelty: conversion events over exposure weeks
            novelty=None
            if ev is not None:
                cev=ev[ev.event.str.contains(name.replace("exp_",""),na=False)]
                if len(cev)>300:
                    ce=cev.merge(grp,on="user_id"); ce["wk"]=pd.to_datetime(ce.ts).dt.isocalendar().week
                    wk=ce.groupby(["wk","group"]).size().unstack(fill_value=0)
                    if "treatment" in wk and "control" in wk and len(wk)>=5:
                        ratio=(wk["treatment"]/(wk["control"]+1)).values
                        slope=np.polyfit(range(len(ratio)),ratio,1)[0]
                        if ratio[0]>1.4 and ratio[-1]<1.05 and slope<0:
                            novelty=(round(ratio[0],2),round(ratio[-1],2))
            if novelty:
                add("experiment_novelty",f"{name}: treatment effect DECAYS across exposure weeks ({novelty[0]}x → {novelty[1]}x). Cumulative '+{lift:.1%}' is a novelty artifact; steady-state effect ≈ 0. Do not ship on cumulative readout.",
                    "MEASURED",f"weekly treated/control ratio",dict(week1=novelty[0],week_last=novelty[1],cum_lift=round(lift,3)),name=name,verdict="novelty_decay",goal=goal)
            elif hte:
                add("experiment_hte",f"{name}: overall +{lift:.1%}, but effect concentrated in {hte[0]} (+{hte[1]:.1%}); ship segmented / re-evaluate rollout",
                    "MEASURED",f"segment={hte[0]}",dict(overall=round(lift,3),segment_lift=round(hte[1],3)),name=name,verdict="ship_segmented",segment=hte[0],goal=goal)
            else:
                add("experiment_lift",f"{name}: treatment {r[0]:.1%} vs control {r[1]:.1%} (+{lift:.1%}, p={r[2]:.1e}); SRM clean",
                    T(r[2]) if r else "HYPOTHESIS",f"exposures['{name}']",dict(lift=round(lift,3),p=(r[2] if r else 1)),name=name,verdict=verdict,goal=goal)

    # P14: event-time-aligned pre/post, split by categoricals
    if ev is not None and monthly and u is not None:
        enable_evs=[e for e in ev.event.unique() if re.search(r"(enabled|activated|connected)$",e)]
        for eev in enable_evs:
            t0=ev[ev.event==eev].groupby("user_id").ts.min()
            if len(t0)<50: continue
            for tbl in monthly.values():
                if "month" not in tbl or "account_id" not in tbl: continue
                for mc in [c for c in tbl.columns if _is_num(tbl[c]) and c!="account_id"]:
                    rows=[]
                    tt=tbl.copy(); tt["p"]=pd.PeriodIndex(tt.month,freq="M")
                    for uid,ts0 in t0.items():
                        pm=pd.Period(ts0,freq="M")
                        mo=tt[tt.account_id==uid]
                        pre=mo[(mo.p>=pm-3)&(mo.p<pm)][mc].mean(); post=mo[(mo.p>pm)&(mo.p<=pm+3)][mc].mean()
                        if pre and pre>0 and not np.isnan(post): rows.append((uid,post/pre))
                    if len(rows)<50: continue
                    rr=pd.DataFrame(rows,columns=["user_id","ratio"]).merge(u,on="user_id")
                    pooled=rr.ratio.mean()
                    for d in [c for c in rr.columns if _is_str(rr[c]) and c!="user_id" and rr[c].nunique()<=8]:
                        gm=rr.groupby(d).ratio.agg(["mean","count"])
                        hot=gm[(gm["mean"]>=pooled+0.15)&(gm["count"]>=15)]
                        if len(hot) and gm["mean"].min()<pooled+0.08:
                            tt_,pv=stats.ttest_ind(rr[rr[d].isin(hot.index)].ratio,rr[~rr[d].isin(hot.index)].ratio)
                            if pv<0.01:
                                add("event_aligned_prepost",
                                    f"post-'{eev}' {mc} grows {hot['mean'].mean():.2f}x in {d}∈{sorted(hot.index)} vs {gm['mean'].min():.2f}x elsewhere (pooled {pooled:.2f}x looks ignorable)",
                                    T(pv),f"post_pre_ratio split by {d}",dict(hot=round(float(hot['mean'].mean()),2),cold=round(float(gm['mean'].min()),2),pooled=round(pooled,2),p=float(pv)),
                                    event=eev,metric=mc,split_dim=d,hot_values=sorted(hot.index))
    return F

# ══════════ v5.1 POST-PROCESSING: leakage → replication → supersede ══════════
def _stem(x): 
    x=re.sub(r"[^a-z]","",str(x).lower())
    for suf in ("edmonth","month","flag","ed","ing","s"):
        if x.endswith(suf) and len(x)>len(suf)+3: x=x[:-len(suf)]
    return x

def leakage_filter(findings, quarantine):
    keep=[]
    for f in findings:
        goal=f.get("goal","")
        gstem=_stem(goal); leak=False; why=""
        cand=f.get("feature") or f.get("dimension") or f.get("action") or ""
        if cand and gstem and len(gstem)>=4 and (gstem in _stem(cand) or _stem(cand) in gstem) and _stem(cand)!="":
            leak=True; why=f"name kinship: '{cand}' ~ '{goal}'"
        st=f.get("stats",{})
        if f["type"] in ("flag_vs_goal","cat_concentration","early_action_threshold"):
            gap=abs(st.get("gap",0))
            if gap>=0.90: leak=True; why=why or f"deterministic split (gap {gap:.0%})"
            if "100%" in f.get("claim","") and (" 0%" in f.get("claim","") or "vs 0%" in f.get("claim","")):
                leak=True; why=why or "100%-vs-0% determinism"
        if leak:
            f["quarantine_reason"]=why; quarantine.append(f)
        else: keep.append(f)
    return keep

def replication_gate(u, findings, quarantine, leads):
    """Split-half (user_id hash) replication for scan-heavy families."""
    if u is None: return findings
    import hashlib
    h=u.user_id.astype(str).map(lambda x: int(hashlib.md5(x.encode()).hexdigest(),16)%2==0)
    A,B=u[h.values],u[~h.values]
    def gap(df,mask,g):
        a=df[mask(df)]; b=df[~mask(df)]
        if min(len(a),len(b))<25: return None,None
        p1,p2=a[g].mean(),b[g].mean()
        se=np.sqrt(p1*(1-p1)/len(a)+p2*(1-p2)/len(b)+1e-12)
        pv=stats.norm.sf(abs((p1-p2)/se))*2
        return p1-p2,pv
    keep=[]
    for f in findings:
        fam=f["type"]; g=f.get("goal")
        if fam not in ("flag_vs_goal","cat_concentration","nway_offset","simpson","flag_interaction") or g not in (u.columns if u is not None else []):
            keep.append(f); continue
        if fam=="flag_vs_goal": m=lambda d,f=f: d[f["feature"]]
        elif fam=="cat_concentration": m=lambda d,f=f: d[f["dimension"]]==f["value"]
        elif fam=="nway_offset": m=lambda d,f=f: np.logical_and.reduce([d[dd]==vv for dd,vv in zip(f["dims"],f["low_cell"])])
        elif fam=="flag_interaction": m=lambda d,f=f: d[f["features"][0]]&d[f["features"][1]]
        else: m=lambda d,f=f: (d[f["segment_dim"]]==f["segment"])&d[f["feature"]]  # simpson: test within segment
        try:
            if fam=="simpson":
                ga,pa=gap(A[A[f["segment_dim"]]==f["segment"]],lambda d,f=f:d[f["feature"]],g)
                gb,pb=gap(B[B[f["segment_dim"]]==f["segment"]],lambda d,f=f:d[f["feature"]],g)
            else:
                ga,pa=gap(A,m,g); gb,pb=gap(B,m,g)
        except Exception: keep.append(f); continue
        if ga is None or gb is None or pa is None or pb is None:  # cells too small to split — keep but demote
            f["evidence"]="INFERRED"; f["replication"]="cells too small for split-half — directional lead"; leads.append(f); continue
        za=stats.norm.isf(max(pa,1e-300)/2)*np.sign(ga); zb=stats.norm.isf(max(pb,1e-300)/2)*np.sign(gb)
        stouffer=abs(za+zb)/np.sqrt(2); pcomb=stats.norm.sf(stouffer)*2
        strict=(np.sign(ga)==np.sign(gb) and abs(gb)>=0.5*abs(ga) and abs(ga)>=0.5*abs(gb) and max(pa,pb)<0.03)
        if strict or (np.sign(ga)==np.sign(gb) and pcomb<0.001 and min(abs(ga),abs(gb))>=0.25*max(abs(ga),abs(gb))):
            f["replication"]=f"split-half PASS (Δa={ga:+.2f}, Δb={gb:+.2f}, combined p={pcomb:.1e})"; keep.append(f)
        elif np.sign(ga)==np.sign(gb) and pcomb<0.05:
            f["evidence"]="HYPOTHESIS"; f["replication"]=f"directional lead — sign-consistent but underpowered (Δa={ga:+.2f}, Δb={gb:+.2f}, combined p={pcomb:.2g})"
            leads.append(f)
        else:
            f["quarantine_reason"]=f"failed split-half replication (Δa={ga:+.2f} p={pa:.2g}, Δb={gb:+.2f} p={pb:.2g})"
            quarantine.append(f)
    return keep

def model_dedup(can, findings):
    u=can["users"]; ev=can["events"]; ex=can["exposures"]
    """v5.2: suppress n-way cells already explained by reported drivers (model-based shadow removal)."""
    if u is None: return findings
    import pandas as _pd
    try: from sklearn.linear_model import LogisticRegression
    except ImportError: return findings
    bygoal={}
    for f in findings:
        g=f.get("goal")
        if g: bygoal.setdefault(g,[]).append(f)
    keep=[]
    for f in findings:
        if f["type"]!="nway_offset": keep.append(f); continue
        g=f["goal"]
        drivers=[]
        for d in bygoal.get(g,[]):
            if d["type"]=="flag_vs_goal": drivers.append(("flag",d["feature"]))
            if d["type"]=="cat_concentration": drivers.append(("cat",d["dimension"],d["value"]))
            if d["type"]=="flag_interaction": drivers+= [("flag",x) for x in d["features"]]+[("inter",tuple(d["features"]))]
            if d["type"]=="simpson": drivers.append(("simp",d["feature"],d["segment_dim"],d["segment"]))
            if d["type"] in ("experiment_lift","experiment_hte","experiment_novelty","experiment_srm"): drivers.append(("exp",d["name"]))
            if d["type"]=="early_action_threshold": drivers.append(("thr",d["action"],d["k"]))
            if d["type"]=="sequence_order": drivers.append(("seq",d["first"],d["second"]))
        if not drivers or g not in u.columns: keep.append(f); continue
        X=pd.DataFrame(index=u.index)
        for dr in drivers:
            if dr[0]=="flag" and dr[1] in u: X[f"f_{dr[1]}"]=u[dr[1]].astype(int)
            elif dr[0]=="cat" and dr[1] in u: X[f"c_{dr[1]}_{dr[2]}"]=(u[dr[1]]==dr[2]).astype(int)
            elif dr[0]=="inter" and all(x in u for x in dr[1]): X["x_"+"_".join(dr[1])]=(u[dr[1][0]]&u[dr[1][1]]).astype(int)
            elif dr[0]=="simp" and dr[1] in u and dr[2] in u: X[f"s_{dr[1]}_{dr[3]}"]=((u[dr[2]]==dr[3])&u[dr[1]]).astype(int)
            elif dr[0]=="exp" and ex is not None:
                grp=ex[ex.experiment==dr[1]][["user_id","group"]]
                t=u[["user_id"]].merge(grp,on="user_id",how="left")
                X[f"e_{dr[1]}"]=(t["group"].values=="treatment").astype(int)
            elif dr[0]=="thr" and ev is not None and "signup_date" in u:
                sd=u.set_index("user_id").signup_date
                sub=ev[ev.event==dr[1]].merge(sd.rename("sd"),left_on="user_id",right_index=True)
                sub=sub[(_pd.to_datetime(sub.ts)-_pd.to_datetime(sub.sd)).dt.days<=7]
                cnt=sub.groupby("user_id").size().reindex(u.user_id).fillna(0)
                X[f"t_{dr[1]}"]=(cnt.values>=dr[2]).astype(int)
            elif dr[0]=="seq" and ev is not None:
                f1=ev[ev.event==dr[1]].groupby("user_id").ts.min(); f2=ev[ev.event==dr[2]].groupby("user_id").ts.min()
                o=(f1.reindex(u.user_id)<f2.reindex(u.user_id)).fillna(False)
                X[f"q_{dr[1]}"]=o.values.astype(int)
        if X.shape[1]==0: keep.append(f); continue
        try:
            m=LogisticRegression(max_iter=200).fit(X,u[g].astype(int))
            pred=m.predict_proba(X)[:,1]
        except Exception: keep.append(f); continue
        try:
            mask=np.logical_and.reduce([u[dd]==vv for dd,vv in zip(f["dims"],f["low_cell"])])
        except Exception: keep.append(f); continue
        if mask.sum()<20: keep.append(f); continue
        obs=u[mask][g].mean(); exp=pred[mask.values if hasattr(mask,'values') else mask].mean(); base=u[g].mean()
        # if the drivers' model already predicts most of the cell's deviation, it's a shadow
        if abs(obs-base)>1e-9 and abs(obs-exp) < 0.5*abs(obs-base):
            f["suppressed_as"]=f"shadow: drivers explain cell ({exp:.0%} predicted vs {obs:.0%} observed, base {base:.0%})"
            continue
        keep.append(f)
    return keep

def supersede(findings):
    keep=list(findings)
    inter={(tuple(sorted(f["features"])),f["goal"]):f["stats"]["gap"] for f in keep if f["type"]=="flag_interaction"}
    if inter:
        drop=set()
        for i,f in enumerate(keep):
            if f["type"]=="flag_vs_goal":
                for (pair,g),ig in inter.items():
                    if f.get("goal")==g and f.get("feature") in pair and ig>abs(f["stats"].get("gap",0)):
                        drop.add(i)
        keep=[f for i,f in enumerate(keep) if i not in drop]
    # interaction_seasonality: keep strongest per metric among overlapping cohorts
    seas=[f for f in keep if f["type"]=="interaction_seasonality"]
    if len(seas)>1:
        best={}
        for f in seas:
            k=f["metric"]
            if k not in best or f["stats"]["dip"]<best[k]["stats"]["dip"]: best[k]=f
        keep=[f for f in keep if f["type"]!="interaction_seasonality" or f is best[f["metric"]]]
        for f in keep:
            if f["type"]=="interaction_seasonality": f["superseded_variants"]=len(seas)-1
    # nway: a superset-cell duplicate of a kept subset cell is redundant — keep the tighter (subset) one
    nws=[f for f in keep if f["type"]=="nway_offset"]
    drop_ids=set()
    for a in nws:
        for b in nws:
            if a is b: continue
            da=dict(zip(a["dims"],a["low_cell"])); db=dict(zip(b["dims"],b["low_cell"]))
            if set(da.items())<set(db.items()) and a.get("goal")==b.get("goal"):
                drop_ids.add(id(b))
    keep=[f for f in keep if id(f) not in drop_ids]
    # cat_concentration / flag_vs_goal absorb nway shadows on same goal sharing a dimension
    strong={(f["dimension"],f.get("goal")) for f in keep if f["type"]=="cat_concentration"}
    strongflags={f.get("goal") for f in keep if f["type"] in ("cat_concentration","flag_vs_goal")}
    def _norm(d): return d.replace("_region","").replace("_group","")
    if strong:
        keep=[f for f in keep if not (f["type"]=="nway_offset" and any(_norm(dd)==_norm(sd) and f.get("goal")==g for dd in f.get("dims",[]) for sd,g in strong))]
    # nway shadows of a flag effect: drop nway on a goal that already has a stronger single-driver explanation, unless its cell gap exceeds the flag gap
    flaggaps={}
    for f in keep:
        if f["type"]=="flag_vs_goal": flaggaps[f.get("goal")]=max(flaggaps.get(f.get("goal"),0),abs(f["stats"].get("gap",0)))
    keep=[f for f in keep if not (f["type"]=="nway_offset" and f.get("goal") in flaggaps and f["stats"].get("spread",0)<=flaggaps[f.get("goal")]*1.3)]
    # simpson supersedes nway offsets sharing its segment dim on same goal
    simps=[(f["segment_dim"],f["goal"]) for f in keep if f["type"]=="simpson"]
    if simps:
        keep=[f for f in keep if not (f["type"]=="nway_offset" and any(sd in f.get("dims",[]) and f.get("goal")==g for sd,g in simps))]
    # component_prechurn supersedes component_divergence on same component
    pc={f["component"] for f in keep if f["type"]=="component_prechurn"}
    keep=[f for f in keep if not (f["type"]=="component_divergence" and f.get("decaying") in pc)]
    return keep

# ══════════ v5.3 ADDITIVE BATTERY (panel recommendations 3-7) ══════════
# Design constraints honored: boolean-goal pipeline untouched; every new finding carries its
# own split-half replication (split by ENTITY id, never by row); continuous-metric findings are
# capped one tier below their boolean equivalent (INFERRED max) until the benchmark scores them.

_NUM_PRIORITY=["partnerrevenue","earningsusd","netpartnerrevenuepostrevshare","netrevenue","revenue","earnings","payout"]
_DEN_PRIORITY=["ownedviews","engagedviews","ownedsubscriptionviews","ownedwatchtime","views","watchtime","impressions","sessions"]

def _registry_numerator(reg_state, measures):
    """OQ-2 (registry-driven metric selection): if a CONFIRMED (or STALE-with-alias)
    metric definition maps to a column present in this table's measures, that column
    IS the numerator — human-confirmed truth outranks name heuristics. STALE selections
    still run (invariant 4: downgrade, don't refuse) and the WS-A tier cap demotes them."""
    if not reg_state: return None
    for d in sorted(reg_state.values(), key=lambda x: x.get("confirmed_at") or 0, reverse=True):
        if d.get("kind") != "metric" or d["status"] not in ("CONFIRMED", "STALE"):
            continue
        pm = d.get("proposed_mapping") or {}
        for c in pm.get("columns", []):
            if c in measures: return (c, d["status"])
        for c in d.get("stale_alias_columns", []) or []:
            if c in measures: return (c, "STALE")
    return None

def _pick_measure(measures, priority):
    nm={_norm_col(c):c for c in measures}
    for p in priority:
        for k,c in nm.items():
            if k==p: return c
    for p in priority:
        for k,c in nm.items():
            if p in k and "split" not in k: return c
    return None

def _entity_halves(d, idc):
    import hashlib
    # str(x) not .astype(str): under pandas 3.0 StringDtype, NaN survives astype(str) as float (RT3 bug class)
    h=d[idc].map(lambda x:int(hashlib.md5(str(x).encode()).hexdigest(),16)%2==0)
    return d[h.values], d[~h.values]

def _numerator_candidates(measures):
    """All measure columns that plausibly denote the revenue-like numerator."""
    cands=[]
    for c in measures:
        k=_norm_col(c)
        if any(p in k for p in ("revenue","earning","payout")) and "split" not in k and "fraction" not in k:
            cands.append(c)
    return cands

def rate_dimension_scan(can, ambiguity_guard=False, multi_numerator=False):
    """R4: rate-by-dimension on long tables — (dimension, numerator, denominator) triples.
    Deterministic rates, min-denominator gates, entity-split replication.
    ambiguity_guard (WS-A §2.5 principle at analysis time): if two near-tied numerator
    candidates exist, do NOT silently pick — flag, and emit rate results as INFERRED
    only, pending a confirmed definition."""
    out=[]
    for base,lt in can["long_tables"].items():
        d=lt["df"]
        _rp=_registry_numerator(can.get("_reg"),lt["measures"])
        num=(_rp[0] if _rp else None) or _pick_measure(lt["measures"],_NUM_PRIORITY)
        if num is None: continue
        reg_confirmed = bool(_rp and _rp[1]=="CONFIRMED")
        ambiguous_numerator=False
        # A CONFIRMED definition IS the resolution of numerator ambiguity — the guard
        # must not re-flag what a human already decided (interaction fix, benchmark A08).
        if ambiguity_guard and not reg_confirmed:
            cands=_numerator_candidates(lt["measures"])
            if len(cands)>1:
                sums={c:pd.to_numeric(d[c],errors="coerce").fillna(0).sum() for c in cands}
                vals=sorted(sums.values(),reverse=True)
                if vals[0]>0 and vals[1]/vals[0]>0.3:   # near-tied in scale: a human must choose
                    ambiguous_numerator=True
                    can["issues"].append(
                        f"{base}: AMBIGUOUS numerator — {len(cands)} revenue-like columns of comparable scale "
                        f"({', '.join(sorted(cands))}); picked none for MEASURED claims. Confirm the definition (WS-A) to resolve.")
        den=_pick_measure([m for m in lt["measures"] if m!=num],_DEN_PRIORITY)
        d=d.copy(); d[num]=pd.to_numeric(d[num],errors="coerce").fillna(0)
        if den is not None: d[den]=pd.to_numeric(d[den],errors="coerce").fillna(0)
        dims=[c for c in d.columns if _is_str(d[c]) and c!=lt["id"] and c!=lt.get("date") and 2<=d[c].nunique()<=250
              and d[c].dropna().astype(str).str.len().mean()<=40 and not _norm_col(c).endswith("id")
              and _norm_col(c) not in ("day","date","dt","reportdate")]
        tot_num=d[num].sum(); tot_den=d[den].sum() if den is not None else len(d)
        if tot_num<=0 or tot_den<=0: continue
        overall=tot_num/tot_den
        A,B=_entity_halves(d,lt["id"])
        for dim in dims:
            g=d.groupby(dim).agg(n=(num,"sum"),dn=(den,"sum") if den is not None else (num,"size"))
            gate=max(0.005*tot_den,1000) if den is not None else 30
            g=g[g["dn"]>=gate]
            if g.empty: continue
            g["rate"]=g["n"]/g["dn"].clip(lower=1e-9); g["ratio"]=g["rate"]/overall
            cands=pd.concat([g[g["ratio"]>=1.7].nlargest(1,"ratio"),g[g["ratio"]<=0.6].nsmallest(1,"ratio")])
            for val,row in cands.iterrows():
                reps=[]
                for H in (A,B):
                    hn=H[num].sum(); hd=H[den].sum() if den is not None else len(H)
                    hv=H[H[dim]==val]
                    hvn=hv[num].sum(); hvd=hv[den].sum() if den is not None else len(hv)
                    if hd<=0 or hvd<max(gate*0.2,10): reps.append(None); continue
                    reps.append((hvn/max(hvd,1e-9))/max(hn/hd,1e-9))
                hi=row["ratio"]>1
                if None in reps:
                    ev,rep="INFERRED","cells too small for entity-split replication"
                elif (hi and min(reps)>=1.25) or ((not hi) and max(reps)<=0.8):
                    ev,rep="MEASURED",f"entity-split PASS (ratio_a={reps[0]:.2f}, ratio_b={reps[1]:.2f})"
                else: continue
                unit=f"per unit '{den}'" if den is not None else "per transaction"
                if ambiguity_guard and ambiguous_numerator and ev=="MEASURED" and not multi_numerator:
                    # with multi_numerator ON every candidate is scanned and labeled — nothing is
                    # silently picked, so the MEASURED tier stands (supersedes the downgrade)
                    ev="INFERRED"; rep=rep+"; DOWNGRADED: numerator definition ambiguous — confirm via WS-A"
                out.append(dict(type="rate_by_dimension",evidence=ev,
                    claim=f"{dim}={val}: '{num}' rate {unit} runs {row['ratio']:.2g}x the table average ({row['rate']:.5g} vs {overall:.5g}; denominator {row['dn']:,.0f}) [{base}]",
                    cohort_code=f"df[df['{dim}']=='{val}']  # table: {base}",
                    stats=dict(ratio=round(float(row["ratio"]),2),rate=float(row["rate"]),overall=float(overall),denom=float(row["dn"])),
                    replication=rep,dimension=dim,value=str(val),table=base,
                    metric=num,denominator=den,
                    _score=abs(np.log(max(row["ratio"],1e-9)))*np.log10(max(row["dn"],10))))
    # dedupe 1: same (dimension,value,direction) across tables — keep the largest-denominator table
    best={}
    for f in out:
        k=(f["dimension"],f["value"],f["stats"]["ratio"]>1)
        if k not in best or f["stats"]["denom"]>best[k]["stats"]["denom"]: best[k]=f
    # dedupe 2: aliased dimensions — identical (table, rate, denominator) is the same cohort under two names
    seen={}; ded=[]
    for f in sorted(best.values(),key=lambda f:-f["_score"]):
        k=(f["table"],round(f["stats"]["rate"],14),round(f["stats"]["denom"],2))
        if k in seen:
            seen[k].setdefault("aliases",[]).append(f"{f['dimension']}={f['value']}")
            seen[k]["claim"]=seen[k]["claim"].replace(" [", f" (also: {f['dimension']}={f['value']}) [",1) if "also:" not in seen[k]["claim"] else seen[k]["claim"]
            continue
        seen[k]=f; ded.append(f)
    # dedupe 3: per-table diversity — guarantee each table's best finding a slot before filling by score
    bytab={}
    for f in ded: bytab.setdefault(f["table"],[]).append(f)
    picked=[t[0] for t in bytab.values()]
    rest=[f for f in ded if f not in picked]
    out=(picked+rest)[:10]
    for f in out: f.pop("_score",None)
    return out

def dow_scan_v2(can):
    """R6 extension: day-of-week seasonality on long tables, both directions.
    >=1.6x MEASURED; 1.3-1.6x INFERRED. Entity-split replication."""
    out=[]
    for base,lt in can["long_tables"].items():
        if lt.get("date") is None: continue
        d=lt["df"]
        _rp=_registry_numerator(can.get("_reg"),lt["measures"])
        num=(_rp[0] if _rp else None) or _pick_measure(lt["measures"],_NUM_PRIORITY)
        if num is None: continue
        d=d.copy(); d[num]=pd.to_numeric(d[num],errors="coerce").fillna(0)
        dt=pd.to_datetime(d[lt["date"]].astype(str),format="%Y%m%d",errors="coerce")
        if dt.isna().mean()>0.5: dt=pd.to_datetime(d[lt["date"]].astype(str),errors="coerce")
        if dt.isna().all(): continue
        d["_dow"]=dt.dt.dayofweek
        def wkratio(x):
            daily=x.groupby([dt.reindex(x.index).dt.date.rename('_d'),x["_dow"]>=5])[num].sum().reset_index()
            wk=daily[~daily["_dow"]][num].mean(); we=daily[daily["_dow"]][num].mean()
            if not wk or not we or wk<=0 or we<=0: return None
            return we/wk
        r=wkratio(d)
        if r is None or (0.77<r<1.3): continue
        A,B=_entity_halves(d,lt["id"]); ra,rb=wkratio(A),wkratio(B)
        if ra is None or rb is None or (r>1)!=(ra>1) or (r>1)!=(rb>1): continue
        hi= r>=1.6 or (1/r)>=1.6
        side="weekends" if r>1 else "weekdays"; ratio=r if r>1 else 1/r
        out.append(dict(type="seasonality_dow",evidence="MEASURED" if hi else "INFERRED",
            claim=f"daily '{num}' runs {ratio:.2f}x higher on {side} [{base}]",
            cohort_code=f"dayofweek{'>=5' if r>1 else '<5'}  # table: {base}",
            stats=dict(ratio=round(float(ratio),2)),replication=f"entity-split PASS ({ra:.2f}, {rb:.2f})",metric=num,table=base))
    return out[:2]

def spike_scan(can):
    """R7 (deterministic half): single-entity temporal spikes. Facts only — no event attribution."""
    out=[]
    for base,lt in can["long_tables"].items():
        if lt.get("date") is None: continue
        d=lt["df"]
        _rp=_registry_numerator(can.get("_reg"),lt["measures"])
        num=(_rp[0] if _rp else None) or _pick_measure(lt["measures"],_NUM_PRIORITY)
        if num is None: continue
        d=d.copy(); d[num]=pd.to_numeric(d[num],errors="coerce").fillna(0)
        daily=d.groupby([lt["id"],lt["date"]])[num].sum().reset_index()
        for eid,g in daily[daily[num]>0].groupby(lt["id"]):
            if len(g)<5 or g[num].sum()<5: continue
            mx=g[num].max(); med=g[g[num]<mx][num].median()
            if med and mx>=8*med and mx>=10:
                day=g.loc[g[num].idxmax(),lt["date"]]
                out.append(dict(type="temporal_spike",evidence="MEASURED",
                    claim=f"{lt['id']}={eid}: '{num}' of {mx:,.2f} on {day} — {mx/med:.0f}x its typical active day (median {med:,.2f}) [{base}]",
                    cohort_code=f"df[(df['{lt['id']}']=='{eid}') & (df['{lt['date']}']=={day})]",
                    stats=dict(peak=float(mx),median=float(med),ratio=round(float(mx/med),1)),
                    metric=num,
                    replication="single-entity fact — replication not applicable; attribution requires external context (narrator scope)",
                    _score=mx))
    out=sorted(out,key=lambda f:-f["_score"])[:3]
    for f in out: f.pop("_score",None)
    return out

def cont_goal_scan(can):
    """R3: boolean features x continuous goals. Mann-Whitney + entity-split sign consistency +
    Benjamini-Hochberg FDR across the continuous family. Tier capped at INFERRED until benchmarked."""
    u=can["users"]; goals=can.get("cont_goals",[])
    if u is None or not goals: return []
    bool_feats=[c for c in u.columns if pd.api.types.is_bool_dtype(u[c]) and c not in can["goal_cols"]]
    if not bool_feats: return []
    tests=[]
    for g in goals:
        y=pd.to_numeric(u[g],errors="coerce").fillna(0)
        for f in bool_feats:
            a,b=y[u[f]],y[~u[f]]
            if min(len(a),len(b))<30: continue
            try: pv=stats.mannwhitneyu(a,b,alternative="two-sided").pvalue
            except Exception: continue
            ra=(a.mean()/max(b.mean(),1e-12)) if b.mean()>0 else np.inf
            tests.append(dict(f=f,g=g,p=pv,ratio=ra,ma=a.mean(),mb=b.mean()))
    if not tests: return []
    tests.sort(key=lambda t:t["p"])
    m=len(tests); out=[]
    import hashlib
    h=u.user_id.astype(str).map(lambda x:int(hashlib.md5(x.encode()).hexdigest(),16)%2==0)
    A,B=u[h.values],u[~h.values]
    for i,t in enumerate(tests,1):
        if t["p"]>0.01*i/m: break                     # BH-FDR at q=0.01
        if not (t["ratio"]>=1.5 or t["ratio"]<=0.67): continue
        yA=pd.to_numeric(A[t["g"]],errors="coerce").fillna(0); yB=pd.to_numeric(B[t["g"]],errors="coerce").fillna(0)
        da=yA[A[t["f"]]].mean()-yA[~A[t["f"]]].mean(); db=yB[B[t["f"]]].mean()-yB[~B[t["f"]]].mean()
        if np.sign(da)!=np.sign(db): continue
        out.append(dict(type="cont_flag_vs_goal",evidence="INFERRED",
            claim=f"{t['f']}=True -> mean '{t['g']}' {t['ma']:,.4g} vs {t['mb']:,.4g} ({t['ratio']:.1f}x; Mann-Whitney p={t['p']:.1e}) [continuous family: tier capped at INFERRED pending benchmark]",
            cohort_code=f"df[df['{t['f']}']]",stats=dict(ratio=round(float(t["ratio"]),2),p=float(t["p"])),
            replication=f"entity-split sign-consistent (da={da:+.3g}, db={db:+.3g}); BH-FDR q=0.01 over {m} tests",
            feature=t["f"],goal=t["g"]))
    return out[:6]

def ops_alerts(can):
    """R5: OPERATIONAL ALERTS — deterministic business-invariant rules. 5th output channel;
    no statistics, no gates to corrupt. Capped and ranked by exposure."""
    alerts=[]
    for base,lt in can["long_tables"].items():
        d=lt["df"]; num=_pick_measure(lt["measures"],_NUM_PRIORITY)
        den=_pick_measure([m for m in lt["measures"] if m!=num],_DEN_PRIORITY) if num else None
        if num is None: continue
        d=d.copy(); d[num]=pd.to_numeric(d[num],errors="coerce").fillna(0)
        if den is not None: d[den]=pd.to_numeric(d[den],errors="coerce").fillna(0)
        # A1: status value with activity but zero yield
        for c in d.columns:
            if not _is_str(d[c]) or d[c].nunique()>12 or c==lt["id"]: continue
            g=d.groupby(c).agg(n=(num,"sum"),dn=(den,"sum") if den else (num,"size"))
            for val,row in g.iterrows():
                if row["n"]==0 and row["dn"]>=5000:
                    sub=d[d[c]==val]; per=d.groupby(lt["id"])[c].apply(lambda s,v=val:(s==v).all())
                    ent=d[d[lt["id"]].isin(per[per].index)].groupby(lt["id"])[den if den else num].agg("sum" if den else "size")
                    ent=ent[ent>=300].nlargest(3)
                    names=", ".join(f"{i} ({v:,.0f})" for i,v in ent.items()) or "none fully affected above threshold"
                    alerts.append(dict(sev=float(row["dn"]),
                        text=f"[{base}] '{c}'='{val}' produced ZERO '{num}' across {row['dn']:,.0f} units of activity. Entities 100% in this state: {names}."))
        # A2: top-decile activity, zero yield
        if den is not None:
            e=d.groupby(lt["id"]).agg(n=(num,"sum"),dn=(den,"sum"))
            q=e["dn"].quantile(0.9)
            bad=e[(e["dn"]>=max(q,100))&(e["n"]==0)].nlargest(3,"dn")
            for eid,row in bad.iterrows():
                alerts.append(dict(sev=float(row["dn"]),
                    text=f"[{base}] {lt['id']}={eid}: top-decile activity ({row['dn']:,.0f} '{den}') with ZERO '{num}' — monetization is off or misconfigured."))
        # A3: refund/chargeback presence
        for c in d.columns:
            if re.search(r"refund|chargeback",str(c),re.I):
                mask=d[c].astype(str).str.lower().isin(["true","1","yes"]) if not pd.api.types.is_bool_dtype(d[c]) else d[c]
                k=int(mask.sum())
                if k>0:
                    amt=d.loc[mask,num].sum()
                    alerts.append(dict(sev=float(abs(amt))+k,
                        text=f"[{base}] {k} refund/chargeback row(s), net '{num}' impact {amt:+,.2f}."))
    alerts=sorted(alerts,key=lambda a:-a["sev"])[:8]
    return [a["text"] for a in alerts]

def coverage_notes(can):
    """R1: 'what this analysis could not see' — auto-generated from what actually ran."""
    n=[]
    if can.get("derived_entity"): n.append("Entity table was DERIVED by aggregation — no native attributes/flags, so boolean practice scans (flag-vs-goal, Simpson, interactions) had no substrate at this grain.")
    elif can["users"] is None: n.append("No entity table — all entity-grain primitives (flag-vs-goal, concentration, n-way, Simpson, interactions) did not run.")
    if can["events"] is None: n.append("No event stream — funnel, sequence-order, early-action, error-cohort and pre/post primitives did not run.")
    if can["exposures"] is None: n.append("No experiment exposures — SRM, lift, heterogeneity and novelty-decay checks did not run.")
    if not can["monthly"]: n.append("No monthly metric tables — trend/decay primitives did not run.")
    if not can.get("cont_goals") and not can["goal_cols"]: n.append("No goal metric identified on the entity grain — outcome-linked scans did not run.")
    if can.get("long_tables"):
        txtcols=[c for lt in can["long_tables"].values() for c in lt["df"].columns if _is_str(lt["df"][c]) and lt["df"][c].dropna().astype(str).str.len().mean()>40]
        if txtcols: n.append(f"Free-text columns present but not semantically analyzed ({', '.join(sorted(set(txtcols))[:3])}) — genre/topic/franchise patterns are invisible to this battery (narrator scope).")
    n.append("Temporal spikes are reported as facts only; attribution to real-world events requires external context (narrator scope).")
    n.append("Recall guarantee is conditional on this representation: insights requiring columns marked 'quarantined'/'dropped' in the manifest, or metric shapes outside the primitive library, are out of scope for this run.")
    return n

def run(path, flag_overrides=None):
    flags = FLAGS_MOD.resolve(flag_overrides)
    # ── WS-C (spec §4): continuous fingerprinting on EVERY ingestion, before analysis.
    # The connector-pipeline stand-in: fingerprint each parseable csv, classify drift,
    # flip STALE synchronously on breaking drift (§4.6 last bullet). The agent itself
    # only ever reads statuses from the registry (read contract §4.6).
    drift_events=[]
    if flags["ws_c_drift"]:
        from . import drift as DRIFT
        for f in sorted(glob.glob(f"{path}/*.csv")):
            try:
                df0=_robust_read(f,[],[])
                if df0 is not None:
                    drift_events += DRIFT.classify(path, DRIFT.fingerprint(df0, f))
            except Exception:
                pass  # fingerprinting must never block analysis (invariant 4: downgrade, don't refuse)
    globals()["_SIL_ADAPTERS_ON"]=bool(flags.get("sil_vendor_adapters"))
    can=load_canonical(path)
    # OQ-2 (flag registry_metric_selection): expose the registry to the scan battery so
    # human-confirmed definition mappings outrank name heuristics for numerator choice.
    can["_reg"]=None
    if flags.get("registry_metric_selection"):
        from . import registry as _REGSEL
        _r=_REGSEL.load(path)
        can["_reg"]=_r if _r else None
    findings=battery(can)
    nulls=[f for f in findings if f["type"]=="null_result"]
    findings=[f for f in findings if f["type"]!="null_result"]
    quarantine=[]; leads=[]
    findings=leakage_filter(findings,quarantine)
    findings=replication_gate(can["users"],findings,quarantine,leads)
    findings=supersede(findings)
    findings=model_dedup(can,findings)
    # v5.3: additive scans run AFTER the boolean pipeline completes — they cannot alter it.
    # Each carries a WS-E ablation flag (spec §6.5): flag off = component never built.
    v53=[]
    if flags["rate_scan"]:  v53+=rate_dimension_scan(can, ambiguity_guard=flags["ambiguity_guard"],
                                                     multi_numerator=flags.get("multi_numerator",False))
    if flags["dow_scan"]:   v53+=dow_scan_v2(can)
    if flags["spike_scan"]: v53+=spike_scan(can)
    if flags["cont_goals"]: v53+=cont_goal_scan(can)
    # ── v5.6: analysis router (Phase-2 #2) + niche tier — additive, flag-gated, gated executors
    if flags.get("analysis_router") and any(flags.get(k) for k in
            ("trend_scan","auto_bucketing","multi_numerator","cross_table","lagged_effects")):
        from . import capabilities as CAP
        _pf,_pl=CAP.dispose(can, CAP.propose(can,flags), flags)
        v53+= _pf+_pl
    if flags.get("niche_tier"):
        from . import capabilities as CAP
        v53+=CAP.niche_scan(can)
    # ── v5.7 text/meaning layer (propose→dispose on unstructured text) ──
    if flags.get("text_features"):
        from . import text_features as TXT
        _tf,_tl=TXT.scan_text(can, flags)
        v53+=_tf+_tl
    new_meas=[f for f in v53 if f["evidence"]=="MEASURED"]
    new_inf=[f for f in v53 if f["evidence"]!="MEASURED"]
    findings=findings+new_meas
    leads=leads+new_inf
    # ── WS-A tier-cap rule (spec §2.4, invariant 3): MEASURED requires a fully
    # CONFIRMED, non-stale definition lineage. Prototype lineage = column-name
    # intersection between the finding and each definition's mapped columns.
    tier_caps_applied=0
    if flags["ws_a_registry"]:
        from . import registry as REGISTRY
        reg = REGISTRY.load(path)
        if reg:
            def _cols_of(f):
                cols=set()
                for k in ("metric","goal","feature","dimension","denominator"):
                    if f.get(k): cols.add(str(f[k]))
                return cols
            for coll in (findings, leads):
                for f in coll:
                    cap, note = REGISTRY.tier_cap_for_columns(reg, _cols_of(f))
                    if cap and f.get("evidence")=="MEASURED":
                        f["evidence"]=cap
                        f["claim"]=f["claim"]+f"  [TIER-CAPPED: {note}]"
                        tier_caps_applied+=1
            findings2=[f for f in findings if f["evidence"]=="MEASURED"]
            leads=leads+[f for f in findings if f["evidence"]!="MEASURED"]
            findings=findings2
    return dict(tool=can["tool"],issues=can["issues"],goal_cols=can["goal_cols"],
                cont_goals=can.get("cont_goals",[]),
                findings=findings,leads=leads,null_results=nulls,quarantine=quarantine,
                alerts=ops_alerts(can) if flags["ops_alerts"] else [],
                manifest=can.get("manifest",[]) if flags["manifest"] else [],
                coverage_notes=coverage_notes(can) if flags["manifest"] else [],
                drift_events=drift_events, tier_caps_applied=tier_caps_applied,
                flags={k:v for k,v in flags.items()})
