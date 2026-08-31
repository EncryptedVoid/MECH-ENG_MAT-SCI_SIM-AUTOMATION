"""Wurtzite ZnO -> LAMMPS data (atom_style charge). Type1=Zn(+2), Type2=O(-2).
Orthorhombic supercell: ax=a, ay=a*sqrt(3), az=c.  a=3.2495, c=5.2069, u=0.3820.
Usage: python build_zno.py <nx> <ny> <nz> <outfile>
"""
import sys, numpy as np
a,c,u = 3.2495, 5.2069, 0.3820
nx,ny,nz,out = int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3]),sys.argv[4]
ax,ay,az = a, a*np.sqrt(3), c
basis=[(1,0.0,0.0,0.0),(1,0.5,0.5,0.0),(1,0.5,1/6.,0.5),(1,0.0,2/3.,0.5),
       (2,0.0,0.0,u  ),(2,0.5,0.5,u  ),(2,0.5,1/6.,0.5+u),(2,0.0,2/3.,0.5+u)]
atoms=[]
for i in range(nx):
  for j in range(ny):
    for k in range(nz):
      for (t,fx,fy,fz) in basis:
        atoms.append((t,(i+fx)*ax,(j+fy)*ay,(k+fz)*az))
Lx,Ly,Lz=nx*ax,ny*ay,nz*az
q={1:2.0,2:-2.0}
with open(out,'w') as f:
    f.write("Wurtzite ZnO (type1=Zn q=+2, type2=O q=-2)\n\n%d atoms\n2 atom types\n\n"%len(atoms))
    f.write("0.0 %.6f xlo xhi\n0.0 %.6f ylo yhi\n0.0 %.6f zlo zhi\n\n"%(Lx,Ly,Lz))
    f.write("Masses\n\n1 65.38\n2 15.9994\n\nAtoms # charge\n\n")
    for n,(t,x,y,z) in enumerate(atoms,1):
        f.write("%d %d %.4f %.6f %.6f %.6f\n"%(n,t,q[t],x,y,z))
print("wrote %s: %d atoms L=(%.3f,%.3f,%.3f) netq=%.2f"%(out,len(atoms),Lx,Ly,Lz,sum(q[t] for t,_,_,_ in atoms)))
