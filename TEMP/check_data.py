"""Sanity-check a LAMMPS data file BEFORE running. Usage: python check_data.py file.data"""
import sys, numpy as np
from collections import Counter
fn=sys.argv[1]; txt=open(fn).read()
key='Atoms # atomic' if 'Atoms # atomic' in txt else 'Atoms # charge'
head,body=txt.split(key)
L=[l.split() for l in body.strip().split('\n') if l.strip()]
box=[]
for ln in head.split('\n'):
    if 'xlo' in ln or 'ylo' in ln or 'zlo' in ln:
        box.append((float(ln.split()[0]),float(ln.split()[1])))
Lb=np.array([h-l for l,h in box])
if key.endswith('atomic'):
    t=np.array([int(x[1]) for x in L]); p=np.array([[float(x[2]),float(x[3]),float(x[4])] for x in L]); q=None
else:
    t=np.array([int(x[1]) for x in L]); q=np.array([float(x[2]) for x in L]); p=np.array([[float(x[3]),float(x[4]),float(x[5])] for x in L])
print(f"file      : {fn}")
print(f"atoms     : {len(L)}   types: {dict(Counter(t.tolist()))}")
print(f"box       : {np.round(Lb,3)}")
if q is not None: print(f"net charge: {q.sum():+.4f}   (must be 0)")
# coordination (periodic in x,y only for BP; all for ZnO)
per = np.array([True,True, q is not None])
cut = 2.6 if q is None else 2.3
n=[]
for i in range(len(p)):
    c=0
    d=p-p[i]
    for k in range(3):
        if per[k]: d[:,k]-=Lb[k]*np.round(d[:,k]/Lb[k])
    r=np.linalg.norm(d,axis=1)
    if q is None: c=int(((r<cut)&(r>0.1)).sum())
    else: c=int(((r<cut)&(r>0.1)&(t!=t[i])).sum())
    n.append(c)
print(f"coordination: {dict(Counter(n))}   (BP expect all 3 ; ZnO expect all 4)")
bad=[i for i,x in enumerate(n) if (q is None and x!=3) or (q is not None and x!=4)]
print("RESULT:", "PASS" if not bad else f"FAIL - {len(bad)} atoms wrong coordination")
