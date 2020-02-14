# inserted from ,,/../doc/ase/fix_symmetry_example.py

from __future__ import print_function
import sys
import numpy as np

from ase.atoms import Atoms
from ase.calculators.lj import LennardJones
from ase.spacegroup.symmetrize import FixSymmetry, check_symmetry
from ase.optimize import BFGS
from ase.constraints import UnitCellFilter

# setup an fcc Al cell
a = 2.0/np.sqrt(3.0)
at_prim = Atoms('Al2', positions=[[0,0,0],[a/2.0, a/2.0, a/2.0]],
                cell=[[a,0,0],[0,a,0],[0,0,a]], pbc=[True, True, True])

# without symmetrization
at_unsym = at_prim * [2,2,2]
at_unsym.positions[0,0] += 1.0e-7 # break symmetry by 1e-7
at_init = at_unsym.copy()

calc = LennardJones()
at_unsym.set_calculator(calc)

at_cell = UnitCellFilter(at_unsym)

dyn = BFGS(at_cell)
print("Energy", at_unsym.get_potential_energy())
dyn.run(fmax=0.001)
print("Energy", at_unsym.get_potential_energy())

# with symmetrization
at_sym = at_prim * [2,2,2]
at_sym.positions[0,0] += 1.0e-7 # break symmetry by 1e-7

at_sym.set_calculator(LennardJones())
at_sym.set_constraint(FixSymmetry(at_sym))

at_cell = UnitCellFilter(at_sym)

dyn = BFGS(at_cell)
print("Energy", at_sym.get_potential_energy())
dyn.run(fmax=0.001)
print("Energy", at_sym.get_potential_energy())

print("position difference", np.linalg.norm(at_unsym.get_positions()-at_sym.get_positions()))

print("initial symmetry at 1e-6")
d_init6 = check_symmetry(at_init, 1.0e-6)
print("initial symmetry at 1e-8")
d_init8 = check_symmetry(at_init, 1.0e-8)
print("unsym symmetry")
d_unsym = check_symmetry(at_unsym)
print("sym symmetry")
d_sym = check_symmetry(at_sym)

assert d_init6["number"] == 229
assert d_init8["number"] == 99
assert d_unsym["number"] == 1
assert d_sym["number"] == 229
