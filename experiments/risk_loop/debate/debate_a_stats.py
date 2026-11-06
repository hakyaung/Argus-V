import json, csv, glob, os, statistics as st, collections as C
R='/home/user/Desktop/Argus-V/artifacts/rollouts-v2'
TEST={'F07','F11','F14'}
lab=C.defaultdict(dict)
for r in csv.DictReader(open('/home/user/Desktop/Argus-V/artifacts/label-review-v2/labels-draft-v1/step_labels.csv')):
    if r['family_id'] in TEST: continue
    lab[r['rollout']][int(r['step_index'])]=r
rows=[]
for d in sorted(os.listdir(R)):
    fam=d.split('-')[1]
    if fam in TEST: continue
    p=f'{R}/{d}/rollout.json'
    if not os.path.exists(p): continue
    j=json.load(open(p))
    cell=j['suite']['v2_cell']; O=cell['O']; L=cell['L']
    steps=j['steps']
    gen=[s.get('generated_tokens',0) for s in steps]; pl=[s.get('prompt_length',0) for s in steps]
    att=sum(s.get('attempts',1) for s in steps)
    lb=lab.get(d,{})
    sub=[i for i,r in lb.items() if r['review_action']=='1' and r['action_kind']=='substantive']
    rows.append(dict(d=d,fam=fam,scen=j['scenario_id'],O=O,L=L,dur=j.get('finished_at',0)-j['started_at'],n=len(steps),gen=sum(gen),genmax=max(gen or [0]),pl=max(pl or [0]),att=att,sub=sorted(sub),split=(list(lb.values())[0]['split'] if lb else '?'),dp=cell.get('dp_id')))
json.dump(rows,open(os.path.dirname(__file__)+'/rows.json','w'))
print(len(rows))
for O in 'BGDX':
    rr=[r for r in rows if r['O']==O]
    if not rr: continue
    print(O,len(rr),'dur med %.0f mean %.0f'%(st.median([r['dur'] for r in rr]),st.mean([r['dur'] for r in rr])),'steps %.1f'%st.mean([r['n'] for r in rr]),'gen/traj %.0f'%st.mean([r['gen'] for r in rr]),'gen/step %.0f'%(sum(r['gen'] for r in rr)/sum(r['n'] for r in rr)),'maxprompt %.0f'%st.mean([r['pl'] for r in rr]),'viol traj',sum(1 for r in rr if r['sub']))
for sp in ['train','validation']:
  rr=[r for r in rows if r['O']=='D' and r['split']==sp]
  c=C.Counter(); n=C.Counter()
  for r in rr: n[r['scen']]+=1; c[r['scen']]+= bool(r['sub'])
  print(sp,'D traj',len(rr),'viol',sum(c.values()),'scen',len(n),'scen>0',sum(1 for k in n if c[k]))
  print(sorted([(k,c[k],n[k]) for k in n if c[k]],key=lambda x:-x[1]))
  byL=C.Counter();nL=C.Counter()
  for r in rr: nL[r['L']]+=1; byL[r['L']]+=bool(r['sub'])
  print(' byL',{k:(byL[k],nL[k]) for k in nL})
  # step of first violation
  print(' first viol step',C.Counter(r['sub'][0] for r in rr if r['sub']))
