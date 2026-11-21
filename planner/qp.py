"""Convex QP solver:  min  0.5 x'H x + c'x   s.t.  Aeq x = beq, x >= 0.

Pure Python, no dependencies.  Two ingredients:

* ``find_feasible`` -- a Phase-I primal simplex (Bland's rule) used both to
  find a starting point and as the feasibility oracle for IIS computation.
* ``solve_qp`` -- a classical primal active-set method.  Each iteration
  solves the KKT system of the equality-constrained subproblem over the
  currently FREE variables only; multipliers for the active bounds are then
  read off the gradient.  (The full system is only used when no variable is
  free, a rare degenerate corner.)

Both routines verify their results; ``solve_qp`` only returns when the KKT
conditions hold within tolerance, and reports the residuals it checked.
"""

from .linalg import dot, mat_vec, solve_consistent, InconsistentError


class InfeasibleError(Exception):
    """The constraint set  Aeq x = beq, x >= 0  is empty."""


class SolverError(Exception):
    """The solver failed to converge / certify (should not happen)."""


def _scale_of(Aeq, beq):
    s = 1.0
    for row in Aeq:
        for v in row:
            av = abs(v)
            if av > s:
                s = av
    for v in beq:
        av = abs(v)
        if av > s:
            s = av
    return s


def find_feasible(Aeq, beq, tol=1e-9, max_iter=100000):
    """Find x >= 0 with Aeq x = beq, or return None if infeasible.

    Phase-I simplex with artificial variables and Bland's pivot rule
    (guaranteed to terminate).  Deterministic.
    """
    m = len(Aeq)
    n = len(Aeq[0]) if m else 0
    if m == 0:
        return [0.0] * n if n else []
    scale = _scale_of(Aeq, beq)
    eps = tol * scale

    N = n + m  # variables: original x, then m artificials
    # tableau rows: B^{-1} A  with initial basis = artificial columns.
    # artificial column i starts as sign(beq[i]) * e_i so that the initial
    # basic solution t = |beq| is nonnegative.
    T = []
    for i in range(m):
        row = list(Aeq[i]) + [0.0] * m + [float(beq[i])]
        if beq[i] < 0:
            row = [-v for v in row]
        T.append(row)
    basis = [n + i for i in range(m)]
    # objective: min sum of artificials.  Reduced costs r_j = c_j - cB' Binv A_j
    # with cB = (1,...,1); store them in row `obj`, last entry = -(objective).
    obj = [0.0] * (N + 1)
    for j in range(N):
        cj = 1.0 if j >= n else 0.0
        obj[j] = cj - sum(T[i][j] for i in range(m))
    obj[N] = -sum(T[i][N] for i in range(m))

    for _ in range(max_iter):
        # entering: smallest index with negative reduced cost (Bland)
        enter = -1
        for j in range(N):
            if obj[j] < -eps:
                enter = j
                break
        if enter < 0:
            break  # optimal
        # leaving: min ratio test, ties broken by smallest basic variable
        leave = -1
        best_ratio = None
        for i in range(m):
            a = T[i][enter]
            if a > eps:
                ratio = T[i][N] / a
                if (
                    best_ratio is None
                    or ratio < best_ratio - eps
                    or (abs(ratio - best_ratio) <= eps and basis[i] < basis[leave])
                ):
                    best_ratio = ratio
                    leave = i
        if leave < 0:
            raise SolverError("phase-I simplex: unbounded (bug)")
        # pivot
        piv = T[leave][enter]
        T[leave] = [v / piv for v in T[leave]]
        for i in range(m):
            if i != leave:
                f = T[i][enter]
                if f != 0.0:
                    Ti, Tl = T[i], T[leave]
                    T[i] = [a - f * b for a, b in zip(Ti, Tl)]
        f = obj[enter]
        if f != 0.0:
            obj = [a - f * b for a, b in zip(obj, T[leave])]
        basis[leave] = enter
    else:
        raise SolverError("phase-I simplex: iteration limit exceeded")

    value = -obj[N]
    if value > 1e-7 * scale:
        return None  # infeasible
    x = [0.0] * n
    for i in range(m):
        if basis[i] < n:
            x[basis[i]] = T[i][N]
    # small negative values are simplex round-off; clamp to zero
    return [v if v > 0.0 else 0.0 for v in x]


def _kkt_full(H, c, Aeq, beq, active, n, m):
    """Solve the KKT system with ALL variables present (active fixed at 0).

    Only used when the free set is empty.  Returns (x, lam, mu).
    """
    k = len(active)
    size = n + m + k
    K = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size
    for i in range(n):
        Hi = H[i]
        row = K[i]
        for j in range(n):
            row[j] = Hi[j]
        for r in range(m):
            row[n + r] = Aeq[r][i]
        rhs[i] = -c[i]
    for r in range(m):
        row = K[n + r]
        Ar = Aeq[r]
        for j in range(n):
            row[j] = Ar[j]
        rhs[n + r] = beq[r]
    for s, ai in enumerate(active):
        K[ai][n + m + s] = -1.0
        K[n + m + s][ai] = 1.0
    sol, _ = solve_consistent(K, rhs)
    return sol[:n], sol[n : n + m], sol[n + m :]


def solve_qp(H, c, Aeq, beq, tol=1e-9, max_iter=None, x0=None):
    """Solve  min 0.5 x'Hx + c'x  s.t.  Aeq x = beq, x >= 0.

    H must be symmetric positive semidefinite.  ``x0`` may provide a known
    feasible starting point (skips the Phase-I simplex).  Returns (x, info)
    where info carries the KKT residuals that were verified before
    returning.  Raises InfeasibleError if the feasible set is empty,
    SolverError on numerical failure.
    """
    n = len(c)
    m = len(Aeq)
    if max_iter is None:
        max_iter = 200 + 40 * (n + m)

    if x0 is not None:
        x = [v if v > 0.0 else 0.0 for v in x0]
    elif m == 0:
        x = [0.0] * n
    else:
        x = find_feasible(Aeq, beq, tol)
        if x is None:
            raise InfeasibleError("constraints Aeq x = beq, x >= 0 are infeasible")

    gscale = max(1.0, max(abs(v) for v in c))
    for row in H:
        for v in row:
            gscale = max(gscale, abs(v))
    eps = tol * gscale

    active = set(i for i in range(n) if x[i] <= eps)
    lam = [0.0] * m

    for it in range(1, max_iter + 1):
        free = [i for i in range(n) if i not in active]
        if free:
            # reduced KKT system over free variables:
            #   H_FF x_F + Aeq_F' lam = -c_F
            #   Aeq_F x_F             = beq        (active vars are 0)
            nf = len(free)
            size = nf + m
            K = [[0.0] * size for _ in range(size)]
            rhs = [0.0] * size
            for a in range(nf):
                ia = free[a]
                Ha = H[ia]
                row = K[a]
                for b in range(nf):
                    row[b] = Ha[free[b]]
                for r in range(m):
                    row[nf + r] = Aeq[r][ia]
                rhs[a] = -c[ia]
            for r in range(m):
                row = K[nf + r]
                Ar = Aeq[r]
                for b in range(nf):
                    row[b] = Ar[free[b]]
                rhs[nf + r] = beq[r]
            try:
                sol, _rank = solve_consistent(K, rhs)
            except InconsistentError as exc:
                raise SolverError("KKT system inconsistent (bug): %s" % exc)
            x_new = [0.0] * n
            for a in range(nf):
                x_new[free[a]] = sol[a]
            lam = sol[nf:]
        else:
            # degenerate corner: every variable is fixed at 0; solve the
            # full system once to get bound multipliers
            try:
                x_new, lam, _mu = _kkt_full(H, c, Aeq, beq, sorted(active), n, m)
            except InconsistentError as exc:
                raise SolverError("KKT system inconsistent (bug): %s" % exc)

        xscale = max(1.0, max(abs(v) for v in x_new))
        if min(x_new) >= -1e-7 * xscale:
            # candidate optimum: check bound multipliers via the gradient
            # g = Hx + c + Aeq'lam;  KKT wants g = 0 on free, g >= 0 on active
            x_cand = [v if v > 0.0 else 0.0 for v in x_new]
            g = [v + cv for v, cv in zip(mat_vec(H, x_cand), c)]
            for r in range(m):
                lr = lam[r]
                if lr != 0.0:
                    Ar = Aeq[r]
                    for i in range(n):
                        g[i] += lr * Ar[i]
            worst_mu = 0.0
            worst_idx = -1
            stat = 0.0
            for i in range(n):
                if i in active:
                    if g[i] < worst_mu - eps:
                        worst_mu = g[i]
                        worst_idx = i
                else:
                    stat = max(stat, abs(g[i]))
            if worst_idx < 0:
                eq_res = max(
                    (abs(a - b) for a, b in zip(mat_vec(Aeq, x_cand), beq)),
                    default=0.0,
                )
                info = {
                    "iterations": it,
                    "stationarity_residual": stat,
                    "equality_residual": eq_res,
                    "min_variable": min(x_cand) if x_cand else 0.0,
                }
                if stat > 1e-6 * gscale or eq_res > 1e-6 * _scale_of(Aeq, beq):
                    raise SolverError(
                        "KKT residuals too large: stat=%.3e eq=%.3e" % (stat, eq_res)
                    )
                return x_cand, info
            # accept the point and drop the bound with the most negative
            # multiplier (ties resolved by smallest index via strict <)
            x = x_cand
            active.discard(worst_idx)
            continue

        # line search from x toward x_new (both satisfy the equalities)
        d = [xn - xc for xn, xc in zip(x_new, x)]
        t = 1.0
        blocker = -1
        for i in range(n):
            if d[i] < 0.0:
                ti = x[i] / (-d[i])
                if ti < 0.0:
                    ti = 0.0
                # smallest ratio wins; ties go to the smallest index (Bland)
                if ti < t - 1e-12 or (blocker < 0 and ti <= t + 1e-12):
                    t = ti
                    blocker = i
        if t < 0.0:
            t = 0.0
        x = [xc + t * dc for xc, dc in zip(x, d)]
        if blocker >= 0:
            x[blocker] = 0.0
            active.add(blocker)
        # if t == 1 with no blocker, the next iteration's multiplier check
        # decides optimality
    raise SolverError("active-set method did not converge in %d iterations" % max_iter)
