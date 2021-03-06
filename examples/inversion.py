import logging
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.tri as tri

import tectosaur_topo as tt
from tectosaur.mesh.refine import refine_to_size

from okada import make_meshes
import scipy.sparse

def add_hill(surf):
    hill_height = 0.2
    hill_R = 0.5
    C = [0,0]
    x, y = surf[0][:,0], surf[0][:,1]
    z = hill_height * np.exp(
        -(((x - C[0]) / hill_R) ** 2 + ((y - C[1]) / hill_R) ** 2)
    )
    surf[0][:,2] = z


def build_soln_to_obs_map(m, obs_pt_idxs, which_dims):
    tris = m.get_tris('surf')
    surf_pts_map = np.unique(tris)
    surf_pts = m.pts[surf_pts_map]
    soln_to_obs = scipy.sparse.dok_matrix((
        surf_pts.shape[0] * len(which_dims),
        m.tris.shape[0] * 9
    ))
    done_pts = dict()
    for i in range(tris.shape[0]):
        for b in range(3):
            if tris[i,b] in done_pts:
                continue
            if tris[i,b] not in obs_pt_idxs:
                continue
            done_pts[tris[i,b]] = 1
            for d in which_dims:
                out_idx = tris[i,b] * len(which_dims) + d
                soln_to_obs[out_idx, i * 9 + b * 3 + d] = 1.0
    assert(soln_to_obs.shape[0] == soln_to_obs.getnnz())
    return soln_to_obs

def build_gfs(surf, fault, sm, pr, **kwargs):
    gfs = []
    for i in range(fault[1].shape[0]):
        for b in range(3):
            for d in range(3):
                print(i, b, d, fault[1].shape[0])
                slip = np.zeros((1, 3, 3))
                slip[0,b,d] = 1.0
                subfault_tris = np.array([[0,1,2]])
                subfault_pts = fault[0][fault[1][i,:]]
                subfault_refined, refined_slip = refine_to_size(
                    (subfault_pts, subfault_tris), 0.005,
                    [slip[:,:,0], slip[:,:,1], slip[:,:,2]]
                )
                full_slip = np.concatenate([s[:,:,np.newaxis] for s in refined_slip], 2).flatten()
                print('tris: ' + str(subfault_refined[1].shape[0]))
                pts, tris, fault_start_idx, soln = tt.forward(
                    surf, subfault_refined, full_slip, sm, pr, **kwargs
                )
                gfs.append(soln[:(fault_start_idx * 9)])
    return gfs

def get_vert_vals_linear(m, x):
    vert_n_tris = [0 for i in range(m[0].shape[0])]
    for i in range(m[1].shape[0]):
        for b in range(3):
            vert_n_tris[m[1][i,b]] += 1
    vert_vals = np.zeros(m[0].shape[0])
    for i in range(m[1].shape[0]):
        for b in range(3):
            vert_vals[m[1][i,b]] += x[i,b]
    vert_vals /= vert_n_tris
    return vert_vals

def slip_constraints(fault):
    from tectosaur.constraint_builders import continuity_constraints, \
        all_bc_constraints, free_edge_constraints
    from tectosaur.constraints import build_constraint_matrix
    cs = continuity_constraints(fault[1], np.zeros((0,3)))
    cm, c_rhs = build_constraint_matrix(cs, fault[1].shape[0] * 9)
    np.testing.assert_almost_equal(c_rhs, 0.0)
    return cm

def main():
    fault_L = 1.0
    top_depth = -0.5
    w = 10
    n_surf = 20
    n_fault = max(2, n_surf // 5)
    sm = 1.0
    pr = 0.25
    cfg = dict(
        log_level = logging.INFO,
        preconditioner = 'ilu'
    )

    flat_surf, fault = make_meshes(fault_L, top_depth, w, n_surf, n_fault)
    hill_surf = (flat_surf[0].copy(), flat_surf[1].copy())
    add_hill(hill_surf)

    slip = np.array([[1, 0, 0] * fault[1].size]).flatten()
    forward_system = tt.forward_assemble(hill_surf, fault, sm, pr, **cfg)
    m = forward_system[0]
    pts, tris, fault_start_idx, soln = tt.forward_solve(
        forward_system, slip, **cfg
    )

    # Inversion parameters
    which_dims = [0, 1]
    obs_pt_idxs = m.get_pt_idxs('surf')
    reg_param = 0.003
    inv_surf = flat_surf

    soln_to_obs = build_soln_to_obs_map(forward_system[0], obs_pt_idxs, which_dims)

    # For some reason, the whole problem behaves funny when I constrain the slip to be continuous!?
    # slip_cm = slip_constraints(fault)

    u_hill = soln_to_obs.dot(soln)

    n_surf = m.n_dofs('surf')
    n_slip = m.n_dofs('fault')
    # n_slip_c = slip_cm.shape[1]
    n_data = u_hill.shape[0]

    # since the inv_surf is the same as surf, forward_system doesn't need to be regenerated.
    forward_system = tt.forward_assemble(inv_surf, fault, sm, pr, **cfg)
    adjoint_system = tt.adjoint_assemble(forward_system, sm, pr, **cfg)
    def mv(v):
        # _,_,_,soln = tt.forward_solve(forward_system, slip_cm.dot(v), **cfg)
        # return np.concatenate((soln_to_obs.dot(soln), slip_cm.T.dot(reg_param * slip_cm.dot(v))))
        _,_,_,soln = tt.forward_solve(forward_system, v, **cfg)
        return np.concatenate((soln_to_obs.dot(soln), reg_param * v))

    def rmv(v):
        rhs = soln_to_obs.T.dot(v[:n_data])
        _,_,_,soln = tt.adjoint_solve(adjoint_system, rhs, **cfg)
        # return slip_cm.T.dot(soln) + slip_cm.T.dot(reg_param * slip_cm.dot(v[n_data:]))
        return soln + reg_param * v[n_data:]

    # A = scipy.sparse.linalg.LinearOperator((n_data + n_slip_c, n_slip_c), matvec = mv, rmatvec = rmv)
    # b = np.concatenate((u_hill, np.zeros(n_slip_c)))
    A = scipy.sparse.linalg.LinearOperator((n_data + n_slip, n_slip), matvec = mv, rmatvec = rmv)
    b = np.concatenate((u_hill, np.zeros(n_slip)))
    inverse_soln = scipy.sparse.linalg.lsmr(A, b, show = True)

    # result = slip_cm.dot(inverse_soln[0])
    result = inverse_soln[0]

    vert_vals = get_vert_vals_linear(fault, result.reshape((-1, 3, 3))[:,:,0])
    triang = tri.Triangulation(fault[0][:,0], fault[0][:,2], fault[1])
    refiner = tri.UniformTriRefiner(triang)
    tri_refi, z_test_refi = refiner.refine_field(vert_vals, subdiv=3)

    plt.figure(figsize = (10, 10))
    ax = plt.gca()
    # plt.triplot(triang, lw = 0.5, color = 'white')
    levels = np.linspace(np.min(z_test_refi), np.max(z_test_refi), 19)
    cntf = plt.tricontourf(tri_refi, z_test_refi, levels=levels)
    plt.tricontour(
        tri_refi, z_test_refi, levels=levels,
        linestyles = 'solid', colors=['k'], linewidths=[0.5]
    )

    cbar = plt.colorbar(cntf)
    plt.show()

if __name__ == "__main__":
    main()



# The penalty method seems to not change for W > 100 or so... why is that?
# And it also doesn't converge to the same result as the Green's function approach. Why?
def penalty_method():
    _, flhs, rhs_op, cm, _ = forward_system
    _, alhs, post_op, _, _ = adjoint_system
    x0 = np.zeros(n_surf + n_slip)
    W = 1.0
    tol = 1e-5
    for i in range(5):
        W *= 10.0
        tol /= 10.0
        def mv2(v):
            rows1 = soln_to_obs.dot(v)
            rows2 = W * (flhs.dot(m.get_dofs(v, 'surf')) - rhs_op.dot(m.get_dofs(v, 'fault')))
            rows3 = reg_param * m.get_dofs(v, 'fault')
            return np.concatenate((rows1, rows2, rows3))

        def rmv2(v):
            v1 = v[:n_data]
            v2 = v[n_data:-n_slip]
            v3 = v[-n_slip:]
            y1 = soln_to_obs.T.dot(v1)[:n_surf] + W * alhs.dot(v2)
            y2 = -W * post_op.dot(v2) + reg_param * v3
            return np.concatenate((y1, y2))

        A2 = scipy.sparse.linalg.LinearOperator(
            (n_data + n_surf + n_slip, n_surf + n_slip),
            matvec = mv2, rmatvec = rmv2
        )
        b2 = np.concatenate((u_hill, np.zeros(n_surf + n_slip)))
        b2 -= A2.dot(x0)
        inverse_soln2 = scipy.sparse.linalg.lsmr(A2, b2, show = True, atol = tol, btol = tol)
        x0 += inverse_soln2[0]
    inverse_soln = [m.get_dofs(x0, 'fault')]




# old, but I left this around for comparison...
def gf_check_code():
    # The full forward problem solution should be approximately to the sum of the proper Green's functions.

    filename = 'examples/gfs.npy'
    # np.save(filename, build_gfs(
    #     hill_surf, fault, sm, pr,
    #     log_level = log_level,
    #     use_fmm = False
    # ))
    gfs = np.load(filename).T

    # soln = np.zeros(m.n_dofs('surf'))
    # for i in range(fault[1].shape[0]):
    #     for b in range(3):
    #         soln += gfs.reshape((-1, fault[1].shape[0], 3, 3))[:, i, b, 0]
    # soln = np.concatenate((soln, slip))
    # plt.plot(m.get_dofs(soln, 'surf'), 'b.')
    # plt.plot(soln2, 'r.')
    # plt.show()

    # Check that the matrix-free Green's function matrix vector products are equal
    # to the fully formed Green's function matrix vector products

    # rand_data = np.random.rand(n_data)
    # y1 = rmv(rand_data)
    # y2 = gfs.T.dot(m.get_dofs(soln_to_obs.T.dot(rand_data), 'surf'))
    # plt.plot(y1, 'b.')
    # plt.plot(y2, 'r.')
    # plt.show()

    # rand_slip = np.random.rand(n_slip)
    # x1 = mv(rand_slip)
    # x2 = soln_to_obs.dot(np.concatenate((gfs.dot(rand_slip), rand_slip)))
    # plt.plot(x1, 'b.')
    # plt.plot(x2, 'r.')
    # plt.show()

    # np.testing.assert_almost_equal(x1, x2)
    # np.testing.assert_almost_equal(y1, y2)

    # A = np.concatenate((soln_to_obs[:,:m.n_dofs('surf')].dot(gfs), reg_param * np.identity(n_slip)))
    # b = np.concatenate((u_hill, np.zeros(n_slip)))
    # inverse_soln = np.linalg.lstsq(A, b)

