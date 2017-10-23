import attr
import numpy as np

import tectosaur
from tectosaur.mesh.combined_mesh import CombinedMesh
from tectosaur.constraint_builders import continuity_constraints, \
    all_bc_constraints, free_edge_constraints
from tectosaur.interior import interior_integral
from tectosaur.ops.sparse_integral_op import SparseIntegralOp, FMMFarfieldBuilder
from tectosaur.ops.mass_op import MassOp
from tectosaur.ops.sum_op import SumOp

from tectosaur_topo.solve import iterative_solve

import logging
logger = logging.getLogger(__name__)

@attr.s
class Result:
    pass

def solve_topo(surf, fault, fault_slip, sm, pr):
    float_type = np.float32
    k_params = [sm, pr]

    m = CombinedMesh([('surf', surf), ('fault', fault)])

    cs = continuity_constraints(
        m.get_piece_tris('surf'), m.get_piece_tris('fault'), m.pts
    )
    cs.extend(all_bc_constraints(
        m.get_start('fault'), m.get_past_end('fault'), fault_slip
    ))
    cs.extend(free_edge_constraints(m.get_piece_tris('surf')))

    mass_op = MassOp(3, m.pts, m.tris)

    T_op = SparseIntegralOp(
        6, 2, 5, 2.0,
        'elasticT3', k_params, m.pts, m.tris,
        float_type,
        farfield_op_type = FMMFarfieldBuilder(150, 3.0, 450)
    )
    iop = SumOp([T_op, mass_op])

    # results = []
    # for nva in range(5, 11):
    #     print(nva)
    #     iop = SparseIntegralOp(
    #         nva, 2, 5, 2.0,
    #         'elasticH3', k_params, m.pts, m.tris,
    #         float_type,
    #         farfield_op_type = FMMFarfieldBuilder(150, 3.0, 450)
    #     )
    #     results.append(iop.dot(np.ones(iop.shape[1])))
    # import ipdb
    # ipdb.set_trace()

    # iop = SparseIntegralOp(
    #     12, 2, 5, 3.0,
    #     'elasticH3', k_params, m.pts, m.tris,
    #     float_type,
    #     farfield_op_type = FMMFarfieldBuilder(150, 3.0, 450)
    # )

    # from tectosaur.ops.dense_integral_op import DenseIntegralOp
    # dense = DenseIntegralOp(
    #     12, 3, 6, 2.0,
    #     'elasticH3', k_params, m.pts, m.tris,
    #     float_type
    # )
    # import ipdb
    # ipdb.set_trace()

    # U_op = SparseIntegralOp(
    #     6, 2, 5, 2.0,
    #     'elasticU3', k_params, m.pts, m.tris,
    #     float_type,
    #     farfield_op_type = FMMFarfieldBuilder(150, 3.0, 450)
    # )
    # iop2 = SumOp([U_op, mass_op])
    iop2 = None

    # A_op = SparseIntegralOp(
    #     6, 2, 5, 2.0,
    #     'elasticA3', k_params, m.pts, m.tris,
    #     float_type,
    #     farfield_op_type = FMMFarfieldBuilder(150, 3.0, 450)
    # )
    # iop2 = SumOp([A_op, mass_op])

    soln = iterative_solve(iop, cs, iop2 = iop2)

    surf_pts, surf_disp = m.extract_pts_vals('surf', soln)

    return surf_pts, surf_disp, soln

def interior_evaluate(obs_pts, surf, fault, soln, sm, pr):
    float_type = np.float32
    k_params = [sm, pr]
    m = CombinedMesh([('surf', surf), ('fault', fault)])

    interior_disp = -interior_integral(
        obs_pts, obs_pts, (m.pts, m.tris), soln, 'elasticT3', 3, 8, k_params, float_type,
        # fmm_params = [100, 3.0, 3000, 25]
    )
    return interior_disp
