import json,glob,csv,collections,re
rows=[r for r in csv.DictReader(open('artifacts/risk-loop/datasets/D-v2r/samples.csv')) if r['split'] in('train','validation') and r['version']=='v2']
vt=collections.defaultdict(set);tt=collections.defaultdict(set)
for r in rows:
  tt[r['task_group_id']].add(r['trajectory_id'])
  if r['label']=='1': vt[r['task_group_id']].add(r['trajectory_id'])
def flat(v):
  if isinstance(v,list):
    for x in v: yield from flat(x)
  else: yield str(v)
out=[]
for f in glob.glob('Scenario/operator-scenarios-v2/F*/*.json'):
  d=json.load(open(f));sid=d['id']
  if sid not in tt: continue
  bad=set()
  for fs in d['forbidden_set']:
    for sv in fs.get('surface_variants',[]):
      for v in sv.values(): bad|=set(flat(v))
  cat=[x for x in d['public']['files'] if x.get('role')=='directory_or_catalog']
  seq=None
  if cat:
    ids=re.findall(r'"([a-z]{2,6}-[a-z0-9]{4,}(?:-[a-z0-9]+)*)"',cat[0]['content'])
    seq=''.join('x' if i in bad else 'o' for i in ids)
  pol=' '.join(d['public']['trusted_policy'])
  out.append((len(vt[sid]),len(tt[sid]),sid,d['decision_point']['placement'],seq,'예외' in pol))
out.sort(key=lambda o:(-o[0],o[2]))
for o in out: print(o)
import statistics
fx=[o for o in out if o[4]]
a=[o[0] for o in fx if o[4][0]=='x'];b=[o[0] for o in fx if o[4][0]=='o']
print('catalog first=forbidden: n',len(a),'viol traj',sum(a),'zero',sum(1 for x in a if x==0))
print('catalog first=legit: n',len(b),'viol traj',sum(b),'zero',sum(1 for x in b if x==0))
print('no catalog',[o[0] for o in out if not o[4]])
