import csv,re,collections,math
from pathlib import Path
from application_parallel import evaluate_report,norm,WEIGHTS,direction,severity
HERE=Path(__file__).resolve().parent

def master_index():
 rows=list(csv.DictReader((HERE/'marker_intelligence_master.csv').open(encoding='utf-8-sig',newline='')))
 index=collections.defaultdict(list)
 for r in rows:
  for name in {r['original_name'],r['clinical_label']}:
   if norm(name):index[norm(name)].append(r)
 return rows,index

def section_norm(x):
 x=str(x or '').lower();x=re.sub(r'\b(analysis report card|analysis report|report|analysis)\b','',x)
 return norm(x)

def canonicalize(observations, master):
 _,idx=master
 out=[]
 for o in observations:
  candidates={r['control_id']:r for r in idx.get(norm(o['marker']),[])}
  exact=[r for r in candidates.values() if section_norm(r['original_report_category'])==section_norm(o['section'])]
  if norm(o['marker'])=='linolenicacid' and str(o['marker']).startswith(('α','γ')):
   target='M119' if o['marker'].startswith('α') else 'M120'
   matches=[x for x in candidates.values() if x['control_id'].lstrip('0')==target]
   if len(matches)==1:exact=matches
  if len(exact)==1:r=exact[0];method='NAME_AND_SOURCE_SECTION'
  elif len(candidates)==1:r=next(iter(candidates.values()));method='UNIQUE_NAME'
  else:r=None;method='AMBIGUOUS_NAME' if candidates else 'NO_MASTER_NAME_MATCH'
  out.append({**o,'canonical_id':r['control_id'] if r else '', 'identity_method':method,'master_category':r['original_report_category'] if r else '', 'low_interpretation_available':bool(r and r['low_interpretation'].strip()),'high_interpretation_available':bool(r and r['high_interpretation'].strip())})
 return out

def compute(report,engine,rules):
 legacy=evaluate_report(report,engine,rules)
 master=master_index(); obs=canonicalize(legacy['observations'],master)
 byid=collections.defaultdict(list)
 for o in obs:
  if o['canonical_id']:byid[o['canonical_id']].append(o)
 grouped=collections.defaultdict(list)
 for r in rules:grouped[r['pattern_key']].append(r)
 pattern=[];evidence=[]
 for old in legacy['patterns']:
  key=old['pattern'];winners=[];unknown=set()
  for rel in grouped[key]:
   cid=rel.get('canonical_id_candidate','')
   for o in byid.get(cid,[]):
    dr=o['direction']; sv=o['severity']
    qualified=(dr in ('low','high') and rel.get('proposed_'+dr,'').upper()=='YES')
    reason=('OPTIMAL_ZERO' if dr=='optimal' else 'UNKNOWN_DIRECTION' if dr not in ('low','high') else 'DIRECTION_NOT_QUALIFIED' if not qualified else 'SEVERITY_UNKNOWN_CONTEXT_ONLY' if sv not in WEIGHTS else 'QUALIFIED')
    item=dict(pattern=key,canonical_id=cid,marker=o['marker'],section=o['section'],direction=dr or '',severity=sv or '',rule_id=rel['rule_id'],reason=reason,weight=WEIGHTS.get(sv,0) if reason=='QUALIFIED' else 0,identity_method=o['identity_method'],counted=False)
    evidence.append(item)
    if reason=='SEVERITY_UNKNOWN_CONTEXT_ONLY':unknown.add(cid)
  eligible=collections.defaultdict(list)
  for e in evidence:
   if e['pattern']==key and e['reason']=='QUALIFIED':eligible[e['canonical_id']].append(e)
  for cid,items in eligible.items():
   chosen=max(items,key=lambda x:x['weight']);chosen['counted']=True;winners.append(chosen)
   for e in items:
    if e is not chosen:e['reason']='DUPLICATE_CANONICAL_EVIDENCE'
  # Monotone diminishing-returns experimental scale, not validated risk.
  total=sum(x['weight'] for x in winners)
  sections=len({x['section'] for x in winners})
  effective=total*(1+min(max(sections-1,0),5)*0.04) if total else 0
  # B.3.12.3 shadow-only corroboration adjustment. Do not change marker eligibility.
  # One observation is provisional; independent sections strengthen corroboration.
  corroboration_factor=(0.70 if len(winners)==1 else 0.90 if len(winners)==2 else 1.0) if winners else 0.0
  if winners and sections==1:corroboration_factor*=0.90
  adjusted_effective=effective*corroboration_factor
  scores={f'k{k}':round(100*(1-math.exp(-adjusted_effective/k)),2) for k in (8,12,18)}
  pattern.append(dict(pattern=key,existing_score=old['existing_score'],previous_candidate_score=old['candidate_score'],calibrated_candidate_score=scores['k12'],sensitivity_k8=scores['k8'],sensitivity_k18=scores['k18'],qualified_unique_markers=len(winners),qualified_sections=sections,unknown_severity_context_markers=len(unknown),severity_weight_sum=round(total,3),corroboration_factor=round(corroboration_factor,3),raw_effective_evidence=round(effective,3),adjusted_effective_evidence=round(adjusted_effective,3),score_basis='EXPERIMENTAL_CORROBORATED_DIMINISHING_RETURNS_NOT_RISK_PERCENT'))
 for rank,row in enumerate(sorted(pattern,key=lambda r:(-r['calibrated_candidate_score'],r['pattern'])),1):row['candidate_rank']=rank
 return obs,evidence,sorted(pattern,key=lambda r:r['candidate_rank']),legacy
