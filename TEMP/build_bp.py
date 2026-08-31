"""Build monolayer black phosphorus LAMMPS data file (2 atom types = 2 pucker groups).
Geometry derived EXACTLY from Jiang 2017 bond lengths (d_intra=2.224, d_inter=2.244 A)
with lattice constants a=3.296 (zigzag,x), b=4.544 (armchair,y) [Zhang 2025 DFT].
Verified: every P has 2 intra-group bonds (2.224) + 1 inter-group bond (2.244).
Type 1 = B (bottom pucker), Type 2 = T (top pucker)  -> pair_coeff * * bp.sw B T
Usage: python build_bp.py <nx> <ny> <outfile>
"""
import sys, numpy as np
a, b = 3.296, 4.544
d1, d2 = 2.224, 2.244
dy  = np.sqrt(d1**2-(a/2)**2)/2
gap = b/2 - 2*dy
h   = np.sqrt(d2**2-gap**2)
vac = 30.0
nx,ny,out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
basis=[(1,0.0,0.0,0.0),(1,a/2,2*dy,0.0),(2,a/2,2*dy+gap,h),(2,0.0,4*dy+gap,h)]
atoms=[]
for i in range(nx):
    for j in range(ny):
        for (t,x0,y0,z0) in basis:
            atoms.append((t, i*a+x0, j*b+y0, z0+vac/2))
Lx,Ly,Lz = nx*a, ny*b, vac+h
with open(out,'w') as f:
    f.write("Monolayer black phosphorus (type1=B bottom pucker, type2=T top pucker)\n\n")
    f.write("%d atoms\n2 atom types\n\n"%len(atoms))
    f.write("0.0 %.6f xlo xhi\n0.0 %.6f ylo yhi\n0.0 %.6f zlo zhi\n\n"%(Lx,Ly,Lz))
    f.write("Masses\n\n1 30.973762\n2 30.973762\n\nAtoms # atomic\n\n")
    for k,(t,x,y,z) in enumerate(atoms,1):
        f.write("%d %d %.6f %.6f %.6f\n"%(k,t,x,y,z))
print("wrote %s: %d atoms  Lx=%.3f Ly=%.3f  (nx=%d ny=%d)  h=%.4f"%(out,len(atoms),Lx,Ly,nx,ny,h))
