"""B.3.8 read-only report-object parallel evaluator. Never attaches or mutates report."""
import csv, json, hashlib, importlib.util, sys, re, collections
from pathlib import Path
from types import SimpleNamespace
HERE=Path(__file__).resolve().parent
ENGINE_SHA='e006ded4a2e25debc6e3d69cc114a26f6b0476d8e69496fbeb5097b3f915ae3c'
RULES_SHA='5fd4438257aa3396c692f990366128ea46c0eb192435561515dba8275f94dfbc'
WEIGHTS={'mild':.25,'moderate':1.,'severe':2.}

def obj(x):
    if isinstance(x,dict):return SimpleNamespace(**{k:obj(v) for k,v in x.items()})
    if isinstance(x,list):return [obj(v) for v in x]
    return x

def read_rules():
    p=HERE/'rules.csv'
    if hashlib.sha256(p.read_bytes()).hexdigest()!=RULES_SHA:raise ValueError('RULE_REGISTER_GUARD_FAIL')
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def norm(x):return re.sub(r'[^a-z0-9]+','',str(x or '').lower())

def direction(marker,engine):
    explicit=str(engine.safe_get(marker,'direction','') or '').strip().lower()
    if explicit in ('low','high','optimal'):return explicit
    sev=str(engine.safe_get(marker,'severity','') or '').strip().lower()
    if sev in ('normal','optimal','within range'):return 'optimal'
    if 'low' in sev or 'reduc' in sev or 'below' in sev:return 'low'
    if 'high' in sev or 'elevat' in sev or 'above' in sev:return 'high'
    return None

def severity(marker,engine):
    s=str(engine.safe_get(marker,'severity','') or '').lower()
    if any(z in s for z in ('severe','marked','critical')):return 'severe'
    if any(z in s for z in ('moderate','moderately')):return 'moderate'
    if any(z in s for z in ('mild','slight')):return 'mild'
    if s in ('normal','optimal','within range'):return 'normal'
    return None

def evaluate_report(report,engine,rules):
    """Run genuine existing V3 on same report; candidate uses only uniquely identified direction-qualified marker evidence."""
    legacy=engine.detect_root_cause_patterns_v3(report)
    section_map=engine._v3_section_lookup(report)
    all_rules={r.key:r for r in engine.RULES} if hasattr(engine,'RULES') else {r.key:r for r in [engine.ABSORPTION_ASSIMILATION_RULE,engine.TOXIC_BURDEN_RULE,engine.INFLAMMATORY_BARRIER_RULE,engine.NEUROCOGNITIVE_SUPPORT_RULE,engine.MITOCHONDRIAL_ENERGY_RULE,engine.GLYCAEMIC_METABOLIC_RULE,engine.LIPID_TRANSPORT_MEMBRANE_RULE,engine.CONNECTIVE_TISSUE_REPAIR_RULE]}
    by_name=collections.defaultdict(set)
    for r in rules:
        if r['canonical_id_candidate'] and r['identity_status'] in ('EXACT_NAME_UNIQUE','RESOLVED','UNIQUE'):
            by_name[norm(r['preferred_marker'])].add(r['canonical_id_candidate'])
    seen=[];obs=[]
    for secname,sec in section_map.items():
        for m in engine.safe_get(sec,'parameters',[]) or []:
            name=engine.safe_get(m,'display_name',None) or engine.safe_get(m,'source_name',None) or engine.safe_get(m,'name',None) or ''
            ids=by_name.get(norm(name),set())
            cid=next(iter(ids)) if len(ids)==1 else None
            dr=direction(m,engine);sv=severity(m,engine)
            obs.append(dict(section=secname,marker=name,canonical_id=cid,direction=dr,severity=sv,identity='unique' if cid else 'unresolved',reason='' if cid else 'NO_UNIQUE_RULE_IDENTITY'))
    bypattern=collections.defaultdict(list)
    for rule in rules:bypattern[rule['pattern_key']].append(rule)
    evidence=[];results=[]
    for key,pr in all_rules.items():
        dedup=collections.defaultdict(list)
        for o in obs:
            if not o['canonical_id']:continue
            for rel in bypattern.get(key,[]):
                if rel['canonical_id_candidate']!=o['canonical_id']:continue
                d=o['direction'];proposal=rel.get('proposed_'+str(d),'').upper() if d in ('low','high') else ''
                qualified=proposal=='YES'
                if d=='optimal':reason='OPTIMAL_ZERO'
                elif d not in ('low','high'):reason='DIRECTION_UNKNOWN'
                elif not qualified:reason='DIRECTION_NOT_QUALIFIED_OR_UNRESOLVED'
                elif o['severity'] not in WEIGHTS:reason='SEVERITY_UNKNOWN_CONTEXT_ONLY'
                else:reason='QUALIFIED'
                row=dict(pattern=key,canonical_id=o['canonical_id'],marker=o['marker'],section=o['section'],direction=d or '',severity=o['severity'] or '',relationship=rel['rule_id'],proposed_eligibility=proposal,qualification_status=rel['qualification_status'],reason=reason,weight=WEIGHTS.get(o['severity'],0) if reason=='QUALIFIED' else 0)
                dedup[o['canonical_id']].append(row)
        qualified_rows=[]
        for cid,rows in dedup.items():
            # preserve all explanations, but count canonical marker only once per pattern
            winners=[r for r in rows if r['reason']=='QUALIFIED']
            winner=max(winners,key=lambda r:r['weight']) if winners else None
            for row in rows:
                row['counted']=bool(winner is row)
                if row['reason']=='QUALIFIED' and not row['counted']:row['reason']='DUPLICATE_CANONICAL_MARKER'
                evidence.append(row)
            if winner:qualified_rows.append(winner)
        sections={r['section'] for r in qualified_rows};hits=len(qualified_rows)
        # Candidate intentionally conservative: no unconditional base or broad-section credit.
        # Same V3 evidence scoring scale and modifiers; unapproved proposal is NOT a production score.
        items=[engine.EvidenceItem(section=r['section'],marker=r['marker'],severity=r['severity'],direction=r['direction'],weight=r['weight']) for r in qualified_rows]
        stats={'cross_section_hits':len(sections),'preferred_marker_hits':hits,'abnormal_hits':hits,'matched_sections':sorted(sections)}
        # use existing V3 scoring with base neutralised, to prevent score for zero evidence
        from dataclasses import replace
        candidate_rule=replace(pr,base_score=0,preferred_marker_bonus=0)
        candidate_score=engine._v3_pattern_score(candidate_rule,items,stats,engine._v3_report_profile(report)) if items else 0
        results.append(dict(pattern=key,candidate_score=candidate_score,qualified_markers=hits,qualified_sections=len(sections),unknown_severity=sum(1 for rows in dedup.values() for r in rows if r['reason']=='SEVERITY_UNKNOWN_CONTEXT_ONLY'),proposed_only=True))
    original={r['key']:r for r in legacy.debug['evaluated_rules']}
    for row in results:
        row['existing_score']=original.get(row['pattern'],{}).get('score',0)
        row['existing_rank']=None
    existing_order=sorted(results,key=lambda r:-r['existing_score']);candidate_order=sorted(results,key=lambda r:-r['candidate_score'])
    for i,r in enumerate(existing_order,1):r['existing_rank']=i
    for i,r in enumerate(candidate_order,1):r['candidate_rank']=i
    return dict(patterns=sorted(results,key=lambda r:r['candidate_rank']),evidence=evidence,observations=obs,existing_debug=legacy.debug,disclaimer='PROPOSAL_ONLY_NOT_PRODUCTION; candidate and existing scores are not calibrated to equivalent meaning')

def load_engine(path):
    p=Path(path)
    if not p.is_file():raise RuntimeError('ENGINE_SOURCE_MISSING: '+str(p))
    if hashlib.sha256(p.read_bytes()).hexdigest()!=ENGINE_SHA:raise RuntimeError('ENGINE_SOURCE_GUARD_FAIL: '+str(p))
    spec=importlib.util.spec_from_file_location('d38_engine',p);mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
    return mod
