"""Count D-X twin pairs (v2 train/validation) and usable violation contrasts; review-draft labels. Read-only.
  python3 experiments/risk_loop/count_dx_pairs.py > artifacts/risk-loop/data-checks/dx_pairs.txt"""
import csv, collections, json, sys
sys.path.insert(0,'experiments/risk_loop')
GEN={"read_file","write_output","submit_result","notify_requester","bash"}
dom=lambda t: t not in GEN and not t.startswith("request_")
s=[r for r in csv.DictReader(open('artifacts/risk-loop/datasets/D-v2/samples.csv')) if r['version']=='v2']
lab={(r['rollout'],r['step_index']):r for r in csv.DictReader(open('artifacts/label-review-v2/labels-draft-v1/step_labels.csv'))}
AX={'pilotD':('D','L2'),'pilotX':('X','L2'),'cell1':('B','L1'),'cell2':('G','L1'),'cell3':('D','L1'),'cell4':('D','L3'),'cell5':('X','L3'),'cell6':('D','L2v')}
T=collections.defaultdict(lambda: {"steps":[]})
for r in s:
    t=T[r['rollout']]; t.update(split=r['split'],fam=r['family_id'],scen=r['scenario_id'],cond=r['condition'])
    L=lab[(r['rollout'],r['step_index'])]
    t["steps"].append((r['tool'], L['review_action'], L['action_kind']))
for t in T.values():
    st=t["steps"]
    t["subst"]=any(a=='1' and k=='substantive' for _,a,k in st)
    t["anyviol"]=any(a in('1','disputed') for _,a,k in st)
    t["dom_tools"]={tool for tool,_,_ in st if dom(tool)}
    t["viol_tools"]={tool for tool,a,k in st if a=='1' and k=='substantive'}
out={}
for split in ('train','validation'):
    R=collections.Counter(); per_fam=collections.defaultdict(collections.Counter); toolpairs=collections.Counter()
    scen=collections.defaultdict(lambda: collections.defaultdict(list))
    for n,t in T.items():
        if t['split']==split: scen[t['scen']][t['cond']].append(t)
    for sc,cd in scen.items():
        fam=next(iter(next(iter(cd.values()))))['fam'] if False else next(iter(cd.values()))[0]['fam']
        for Dc,Xc,band in (('pilotD','pilotX','L2'),('cell4','cell5','L3')):
            D,X=cd.get(Dc,[]),cd.get(Xc,[])
            if not D or not X: continue
            R[f'{band}_scenarios_with_DX']+=1; R[f'{band}_D_traj']+=len(D); R[f'{band}_X_traj']+=len(X)
            Dv=[d for d in D if d['subst']]; Xe=[x for x in X if x['dom_tools']]
            R[f'{band}_D_violating_traj']+=len(Dv); R[f'{band}_X_with_effect_traj']+=len(Xe)
            R[f'{band}_X_flagged_violation_traj']+=sum(x['anyviol'] for x in X)
            # usable pair: D violation tool also called by some X trajectory in same scenario
            for d in Dv:
                xt=[x for x in X if d['viol_tools'] & x['dom_tools'] and not x['anyviol']]
                if xt:
                    R[f'{band}_usable_pairs_Dviol_traj']+=1; per_fam[fam][band]+=1
                    for tool in d['viol_tools'] & set().union(*[x['dom_tools'] for x in xt]): toolpairs[tool]+=1
    out[split]={"counts":dict(R),"usable_by_family":{k:dict(v) for k,v in per_fam.items()},"tools":dict(toolpairs)}
print(json.dumps(out,ensure_ascii=False,indent=1))
c=collections.Counter((t['split'],t['cond']) for t in T.values()); print(sorted(c.items()))
print("---X flags")
k=collections.Counter(); ex=[]
for n,t in T.items():
    if t['cond'] in ('pilotX','cell5'):
        for tool,a,kind in t['steps']:
            if a in ('1','disputed'):
                k[(t['split'], a, kind or '-', 'domain' if dom(tool) else tool)]+=1
                if a=='1' and kind=='substantive' and len(ex)<6: ex.append((n,tool))
for x in sorted(k.items(), key=lambda z:-z[1])[:14]: print(x)
print(ex)
print("---D (all D cells) violation rate by split")
for sp in ('train','validation'):
    D=[t for t in T.values() if t['split']==sp and t['cond'] in ('pilotD','cell3','cell4','cell6')]
    print(sp, len(D), sum(t['subst'] for t in D))
