"""Unit tests for the QP solver against analytically known solutions."""
import sys
import unittest

sys.path.insert(0, "/workspace")

from planner.qp import solve_qp, find_feasible, InfeasibleError


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


class TestSolveQp(unittest.TestCase):
    def test_equality_constrained(self):
        # min (x-3)^2 + (y-4)^2  s.t. x + y = 5  ->  (2, 3)
        x, info = solve_qp([[2.0, 0.0], [0.0, 2.0]], [-6.0, -8.0],
                           [[1.0, 1.0]], [5.0])
        self.assertTrue(approx(x[0], 2.0) and approx(x[1], 3.0), x)
        self.assertLessEqual(info["stationarity_residual"], 1e-6)

    def test_bound_active(self):
        # min (x+5)^2 s.t. x >= 0  ->  x = 0
        x, _ = solve_qp([[2.0]], [10.0], [], [])
        self.assertTrue(approx(x[0], 0.0), x)

    def test_infeasible_detected(self):
        with self.assertRaises(InfeasibleError):
            solve_qp([[2.0]], [0.0], [[1.0]], [-1.0])

    def test_semidefinite_hessian(self):
        # min (x+y-4)^2  ->  any point with x+y = 4
        x, _ = solve_qp([[2.0, 2.0], [2.0, 2.0]], [-8.0, -8.0], [], [])
        self.assertTrue(approx(x[0] + x[1], 4.0), x)

    def test_degenerate_start(self):
        # min (x-3)^2+(y-1)^2 s.t. x - y = 0  ->  (2, 2);
        # the simplex starts at (0,0) with every bound active
        x, _ = solve_qp([[2.0, 0.0], [0.0, 2.0]], [-6.0, -2.0],
                        [[1.0, -1.0]], [0.0])
        self.assertTrue(approx(x[0], 2.0) and approx(x[1], 2.0), x)

    def test_water_filling_with_clamping(self):
        # min sum (x_i - t_i)^2 s.t. sum x = 20, x >= 0, t = 1..8
        n = 8
        t = [float(i + 1) for i in range(n)]
        H = [[2.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        c = [-2.0 * v for v in t]
        x, _ = solve_qp(H, c, [[1.0] * n], [20.0])
        lam = 13.0 / 6.0  # t_1, t_2 clamp to 0; sum(3..8) - 6*lam = 20
        self.assertTrue(approx(sum(x), 20.0), x)
        for xi, ti in zip(x, t):
            self.assertTrue(approx(xi, max(0.0, ti - lam)), (x, xi, ti))


class TestFindFeasible(unittest.TestCase):
    Aeq = [
        [1, 1, 0, 0],
        [0, 0, 1, 1],
        [1, 0, 1, 0],
        [0, 1, 0, 1],
    ]

    def test_transportation_feasible(self):
        beq = [3.0, 5.0, 4.0, 4.0]
        x = find_feasible(self.Aeq, beq)
        self.assertIsNotNone(x)
        for row, b in zip(self.Aeq, beq):
            self.assertTrue(approx(sum(a * v for a, v in zip(row, x)), b), x)
        self.assertGreaterEqual(min(x), 0.0)

    def test_transportation_infeasible(self):
        # row sums (8) != column sums (9)
        self.assertIsNone(find_feasible(self.Aeq, [3.0, 5.0, 4.0, 5.0]))

    def test_negative_rhs_infeasible(self):
        self.assertIsNone(find_feasible([[1.0, 1.0]], [-1.0]))


if __name__ == "__main__":
    unittest.main()
