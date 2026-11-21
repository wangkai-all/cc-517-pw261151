"""Dense linear-algebra helpers (pure Python, no dependencies).

Only what the QP solver needs: a Gaussian-elimination solver that tolerates
singular-but-consistent square systems (the KKT systems of convex QPs with
semidefinite Hessians are of that kind).
"""


class InconsistentError(Exception):
    """Raised when a linear system has no solution."""


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def mat_vec(A, x):
    return [dot(row, x) for row in A]


def solve_consistent(A, b, tol=1e-10):
    """Solve A x = b for a square, possibly singular but consistent system.

    Gaussian elimination with complete pivoting.  Free variables (from zero
    pivots) are set to 0, which yields *a* solution whenever one exists --
    for the KKT system of a convex QP any solution is a valid minimizer of
    the reduced problem.

    Returns (x, rank).  Raises InconsistentError if the system is
    inconsistent beyond tolerance.
    """
    n = len(A)
    if n == 0:
        return [], 0
    # augmented matrix, rows are [A | b]
    M = [list(A[i]) + [float(b[i])] for i in range(n)]
    scale = max(
        1.0,
        max(abs(v) for row in A for v in row),
        max(abs(v) for v in b),
    )
    perm = list(range(n))  # perm[new_col] = original variable index
    rank = 0
    for col in range(n):
        # complete pivoting over the remaining block
        piv = 0.0
        pi = pj = -1
        for i in range(rank, n):
            Mi = M[i]
            for j in range(col, n):
                v = abs(Mi[j])
                if v > piv:
                    piv, pi, pj = v, i, j
        if piv <= tol * scale:
            break  # remaining coefficient block is numerically zero
        if pi != rank:
            M[rank], M[pi] = M[pi], M[rank]
        if pj != col:
            for r in M:
                r[col], r[pj] = r[pj], r[col]
            perm[col], perm[pj] = perm[pj], perm[col]
        inv = 1.0 / M[rank][col]
        for i in range(rank + 1, n):
            f = M[i][col] * inv
            if f != 0.0:
                Mi = M[i]
                Mr = M[rank]
                for j in range(col, n + 1):
                    Mi[j] -= f * Mr[j]
        rank += 1
    # consistency check on the zeroed-out tail rows
    for i in range(rank, n):
        if abs(M[i][n]) > 1e-8 * scale:
            raise InconsistentError(
                "linear system is inconsistent (residual %.3e)" % abs(M[i][n])
            )
    # back substitution; variables beyond `rank` in the permutation stay 0
    x = [0.0] * n
    for i in range(rank - 1, -1, -1):
        s = M[i][n]
        Mi = M[i]
        for j in range(i + 1, n):
            s -= Mi[j] * x[perm[j]]
        x[perm[i]] = s / Mi[i]
    return x, rank
