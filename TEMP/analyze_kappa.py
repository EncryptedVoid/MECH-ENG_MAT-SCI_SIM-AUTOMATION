"""Compute kappa from Langevin two-bath NEMD output.

Usage:
  python analyze_kappa.py <ebath.dat> <T_profile.dat> <area_A2> <Lbox_A>

Areas (perpendicular to heat flow):
  BP zigzag  (x): Ly*5.24 = 18.176*5.24 = 95.24  A^2
  BP armchair(y): Lx*5.24 = 13.184*5.24 = 69.08  A^2

Method: hot bath ADDS energy (E_hot grows +), cold bath REMOVES (E_cold grows -).
At steady state |dE_hot/dt| ~ |dE_cold/dt| = power Q. Heat crosses the sample
in ONE direction between the baths, so J = Q / A (no factor of 2 here).
Gradient is fit over the LINEAR region BETWEEN the baths only.
"""
import sys, numpy as np
if len(sys.argv)!=5: sys.exit(__doc__)
eb, prof, A, L = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])

# ---- power from bath tallies ----
d=[]
for l in open(eb):
    p=l.split()
    try: d.append([float(p[0]),float(p[1]),float(p[2])])
    except ValueError: continue
d=np.array(d); t,Eh,Ec=d[:,0],d[:,1],d[:,2]
m=t>t.max()*0.4
sh=np.polyfit(t[m],Eh[m],1)[0]     # eV/ps (hot, +)
sc=np.polyfit(t[m],Ec[m],1)[0]     # eV/ps (cold, -)
Q=(abs(sh)+abs(sc))/2              # average magnitude
J=Q/A
bal=abs(abs(sh)-abs(sc))/max(abs(sh),abs(sc))

# ---- gradient over linear region between baths ----
raw=[l for l in open(prof) if not l.lstrip().startswith('#')]
hdr=[i for i,l in enumerate(raw) if len(l.split())==3]
nch=int(raw[hdr[-1]].split()[1]); blk=raw[hdr[-1]+1:hdr[-1]+1+nch]
if nch<6: sys.exit("ERROR: %d chunks -> chunk width wrong, re-run fixed script."%nch)
arr=np.array([[float(x) for x in l.split()] for l in blk]); coord,T=arr[:,1],arr[:,3]
x=coord*L
# find hot (max T) and cold (min T) bin indices; fit strictly between them
ih=int(np.argmax(T)); ic=int(np.argmin(T))
lo,hi=sorted((ih,ic))
# trim one bin off each end of the interior to avoid bath edge effects
seg=slice(lo+1,hi)  if hi-lo>3 else slice(lo,hi+1)
if seg.stop-seg.start<3: sys.exit("ERROR: linear region too short; check profile.")
grad=abs(np.polyfit(x[seg],T[seg],1)[0])

kappa=(J/grad)*1.602176634e-19/(1e-12*1e-10)
print(f"  n_chunks     = {nch}")
print(f"  dE_hot/dt    = {sh:+.4f} eV/ps      dE_cold/dt = {sc:+.4f} eV/ps")
print(f"  bath balance = {bal*100:.1f} %   (want < ~15%)")
print(f"  Q            = {Q:.4f} eV/ps        J = {J:.4e} eV/ps/A^2")
print(f"  dT/dx        = {grad:.4f} K/A   (fit over {seg.stop-seg.start} interior bins)")
print(f"  T span       = {T.min():.1f} - {T.max():.1f} K   (want a GENTLE ~20-40 K span)")
print(f"  KAPPA        = {kappa:.2f} W/m/K     [L = {L:.1f} A]")
print(f"  1/L = {1/L:.6f}   1/kappa = {1/kappa:.6f}")
if bal>0.15: print("  WARNING: bath imbalance high -> run longer.")
if T.max()-T.min()>60: print("  NOTE: gradient large; still ok but gentler is better.")
