# =============================================================================
# Stillinger-Weber potential for single-layer black phosphorus (BP / SLBP)
# For LAMMPS  ->  pair_style sw   (or hybrid/overlay ... sw ...)
# Units: metal (epsilon in eV, sigma in Angstrom; other columns unitless)
#
# SOURCE (verified): Jin-Wu Jiang, "An Empirical Description for the Hinge-Like
#   Mechanism in Single-Layer Black Phosphorus: the Angle-Angle Cross
#   Interaction", Acta Mechanica Solida Sinica (2017),
#   DOI 10.1016/j.camss.2017.04.002.  Appendix A (LAMMPS sw.bp) + Table 4.
#
# BP is PUCKERED: two phosphorus sub-types are required:
#   T = top pucker group,  B = bottom pucker group.
# Your data file must assign P atoms to these two types, and your pair_coeff
# must map them, e.g.:  pair_coeff * * sw bp.sw T B   (order matches your types)
#
# NOTE: this is the plain SW (no angle-angle-cross term). It reproduces the
# phonon spectrum and standard mechanics, which is what thermal-transport work
# uses. It does NOT reproduce BP's negative Poisson's ratio (that needs the
# separate AAC term, not available as a native LAMMPS pair style).
#
# Column format per entry:
# el1 el2 el3  epsilon  sigma   a      lambda  gamma  cos(theta0)  A      B        p q tol
# -----------------------------------------------------------------------------
# intra-group SW2 + SW3
T T T   1.000   0.565   4.940   19.828  1.000  -0.111    4.027  119.005  4 0 0.0
B B B   1.000   0.565   4.940   19.828  1.000  -0.111    4.027  119.005  4 0 0.0
# inter-group SW2
T B B   1.000   0.565   4.940    0.000  1.000  -0.111    4.027  119.005  4 0 0.0
B T T   1.000   0.565   4.940    0.000  1.000  -0.111    4.027  119.005  4 0 0.0
# inter-group SW3
T T B   1.000   0.565   4.940   17.776  1.000  -0.210    0.000  119.005  4 0 0.0
T B T   1.000   0.565   4.940   17.776  1.000  -0.210    0.000  119.005  4 0 0.0
B B T   1.000   0.565   4.940   17.776  1.000  -0.210    0.000  119.005  4 0 0.0
B T B   1.000   0.565   4.940   17.776  1.000  -0.210    0.000  119.005  4 0 0.0
