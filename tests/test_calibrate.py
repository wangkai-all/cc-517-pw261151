"""Requirement-level tests for the calibration engine.

Run:  python3 -m unittest discover -s tests -v
"""
import random
import sys
import unittest

sys.path.insert(0, "/workspace")

from planner.calibrate import calibrate, validate_payload, build_problem, evaluate


def payload_mismatch():
    """Region forecasts (sum 1140), category forecasts (sum 1120), grand
    forecast 1150 and cell forecasts (sum 1080) all disagree."""
    return {
        "regions": ["华北", "华东", "华南"],
        "categories": ["饮料", "零食", "生鲜"],
        "cells": {
            "forecast": [[120, 150, 90], [130, 160, 100], [110, 140, 80]],
            "weight": [[1, 1, 1], [1, 2, 1], [1, 1, 1]],
        },
        "regionTotals": {"forecast": [400, 380, 360], "weight": [4, 4, 4],
                         "lock": [None, None, None]},
        "categoryTotals": {"forecast": [350, 470, 300], "weight": [3, 3, 3],
                           "lock": [None, None, None]},
        "grandTotal": {"forecast": 1150, "weight": 1, "lock": None},
    }


class TestReconciliation(unittest.TestCase):
    def test_aggregates_internally_consistent(self):
        r = calibrate(payload_mismatch())
        self.assertEqual(r["status"], "optimal")
        cells = r["cells"]
        for i, rt in enumerate(r["regionTotals"]):
            self.assertAlmostEqual(sum(cells[i]), rt, places=8)
        for j, ct in enumerate(r["categoryTotals"]):
            self.assertAlmostEqual(sum(row[j] for row in cells), ct, places=8)
        self.assertAlmostEqual(sum(r["regionTotals"]), r["grandTotal"], places=8)
        self.assertAlmostEqual(sum(r["categoryTotals"]), r["grandTotal"], places=8)

    def test_objective_recomputed_and_terms(self):
        p = payload_mismatch()
        r = calibrate(p)
        spec = validate_payload(p)
        prob = build_problem(spec)
        flat = [v for row in r["cells"] for v in row]
        obj, breakdown = evaluate(prob["terms"], flat)
        self.assertAlmostEqual(obj, r["objective"], places=8)
        # every provided forecast appears exactly once in the breakdown
        n_expected = 9 + 3 + 3 + 1
        self.assertEqual(len(r["objectiveTerms"]), n_expected)
        self.assertAlmostEqual(
            sum(t["contribution"] for t in r["objectiveTerms"]), r["objective"], places=8)

    def test_missing_forecast_is_not_treated_as_zero(self):
        """A missing forecast must not enter the objective at all: giving the
        missing slot any weight must leave the solution unchanged."""
        p = payload_mismatch()
        p["cells"]["forecast"][0][0] = None
        p["cells"]["weight"][0][0] = None
        r1 = calibrate(p)
        self.assertEqual(r1["status"], "optimal")
        # 16 terms now (one cell forecast removed)
        self.assertEqual(len(r1["objectiveTerms"]), 15)
        # if the missing cell were treated as forecast=0 with weight 1000,
        # the solution would change dramatically; with correct handling the
        # objective terms simply do not include it
        self.assertFalse(any(t["label"] == "华北×饮料" for t in r1["objectiveTerms"]))

    def test_optimality_against_perturbations(self):
        """No feasible perturbation may improve the objective."""
        p = payload_mismatch()
        p["regionTotals"]["lock"] = [410.0, None, None]
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        spec = validate_payload(p)
        prob = build_problem(spec)
        x = [v for row in r["cells"] for v in row]
        f0, _ = evaluate(prob["terms"], x)
        rng = random.Random(42)
        n = len(x)
        for _ in range(3000):
            d = [rng.uniform(-1, 1) for _ in range(n)]
            # project d onto the null space of Aeq (only one lock here)
            for row in prob["Aeq"]:
                s = sum(a * b for a, b in zip(row, d)) / sum(a * a for a in row)
                d = [di - s * ai for di, ai in zip(d, row)]
            for eps in (0.05, 0.2, 1.0):
                y = [xi + eps * di for xi, di in zip(x, d)]
                if min(y) < -1e-9:
                    continue
                fy, _ = evaluate(prob["terms"], y)
                self.assertGreaterEqual(fy, f0 - 1e-6 * max(1.0, f0))

    def test_locks_are_satisfied(self):
        p = payload_mismatch()
        p["regionTotals"]["lock"] = [400.0, None, None]
        p["categoryTotals"]["lock"] = [None, 500.0, None]
        p["grandTotal"]["lock"] = 1200.0
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        self.assertAlmostEqual(r["regionTotals"][0], 400.0, places=6)
        self.assertAlmostEqual(r["categoryTotals"][1], 500.0, places=6)
        self.assertAlmostEqual(r["grandTotal"], 1200.0, places=6)
        for chk in r["locks"]:
            self.assertAlmostEqual(chk["actual"], chk["value"], places=6)
        self.assertLess(r["checks"]["maxLockResidual"], 1e-6)
        self.assertGreaterEqual(r["checks"]["minCellValue"], -1e-9)

    def test_higher_weight_stays_closer(self):
        """Two conflicting totals forecasts; the heavier one wins."""
        p = {
            "regions": ["甲"], "categories": ["A", "B"],
            "cells": {"forecast": [[None, None]], "weight": [[None, None]]},
            "regionTotals": {"forecast": [100.0], "weight": [10.0], "lock": [None]},
            "categoryTotals": {"forecast": [None, None], "weight": [None, None],
                               "lock": [None, None]},
            "grandTotal": {"forecast": 200.0, "weight": 1.0, "lock": None},
        }
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        # least squares of w1(x-100)^2 + w2(x-200)^2 -> x = (10*100+1*200)/11
        self.assertAlmostEqual(r["grandTotal"], (10 * 100 + 200) / 11, places=6)


class TestTieBreak(unittest.TestCase):
    def test_equal_split_under_single_lock(self):
        """Only the grand total locked: min sum(x^2) splits evenly."""
        p = {
            "regions": ["甲", "乙"], "categories": ["A", "B"],
            "cells": {"forecast": [[None] * 2] * 2, "weight": [[None] * 2] * 2},
            "regionTotals": {"forecast": [None, None], "weight": [None, None],
                             "lock": [None, None]},
            "categoryTotals": {"forecast": [None, None], "weight": [None, None],
                               "lock": [None, None]},
            "grandTotal": {"forecast": None, "weight": None, "lock": 100.0},
        }
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        for row in r["cells"]:
            for v in row:
                self.assertAlmostEqual(v, 25.0, places=6)

    def test_determinism(self):
        r1 = calibrate(payload_mismatch())
        r2 = calibrate(payload_mismatch())
        self.assertEqual(r1, r2)

    def test_min_norm_among_optima(self):
        """No forecasts, one region locked to 100: every feasible point has
        objective 0, the tie-break must pick the min-norm one (50/50)."""
        p = {
            "regions": ["甲"], "categories": ["A", "B"],
            "cells": {"forecast": [[None, None]], "weight": [[None, None]]},
            "regionTotals": {"forecast": [None], "weight": [None], "lock": [100.0]},
            "categoryTotals": {"forecast": [None, None], "weight": [None, None],
                               "lock": [None, None]},
            "grandTotal": {"forecast": None, "weight": None, "lock": None},
        }
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        self.assertAlmostEqual(r["cells"][0][0], 50.0, places=6)
        self.assertAlmostEqual(r["cells"][0][1], 50.0, places=6)
        self.assertAlmostEqual(r["objective"], 0.0, places=9)
        self.assertAlmostEqual(r["checks"]["tieBreakSumSquares"], 2 * 50.0 ** 2, places=4)


class TestConflicts(unittest.TestCase):
    def payload_conflict(self):
        return {
            "regions": ["华北", "华东"],
            "categories": ["饮料", "零食"],
            "cells": {"forecast": [[100, 100], [100, 100]],
                      "weight": [[1, 1], [1, 1]]},
            "regionTotals": {"forecast": [None, None], "weight": [None, None],
                             "lock": [300.0, 300.0]},      # sum 600
            "categoryTotals": {"forecast": [None, None], "weight": [None, None],
                               "lock": [400.0, 400.0]},     # sum 800 -> conflict
            "grandTotal": {"forecast": None, "weight": None, "lock": None},
        }

    def test_conflict_reports_iis(self):
        r = calibrate(self.payload_conflict())
        self.assertEqual(r["status"], "infeasible")
        self.assertTrue(r["conflicts"])
        # every IIS member is a lock; all four locks participate here
        labels = {(c["level"], c["label"]) for c in r["conflicts"]}
        self.assertEqual(labels, {("region", "华北"), ("region", "华东"),
                                  ("category", "饮料"), ("category", "零食")})

    def test_removing_any_iis_lock_restores_feasibility(self):
        base = self.payload_conflict()
        r = calibrate(base)
        self.assertEqual(r["status"], "infeasible")
        for c in r["conflicts"]:
            p = self.payload_conflict()
            if c["level"] == "region":
                p["regionTotals"]["lock"][c["index"]] = None
            elif c["level"] == "category":
                p["categoryTotals"]["lock"][c["index"]] = None
            else:
                p["grandTotal"]["lock"] = None
            r2 = calibrate(p)
            self.assertEqual(r2["status"], "optimal",
                             "removing %s should fix it" % c["label"])

    def test_negative_lock_is_infeasible(self):
        p = self.payload_conflict()
        p["regionTotals"]["lock"] = [-5.0, None]
        p["categoryTotals"]["lock"] = [None, None]
        r = calibrate(p)
        self.assertEqual(r["status"], "infeasible")
        self.assertEqual(len(r["conflicts"]), 1)
        self.assertEqual(r["conflicts"][0]["label"], "华北")

    def test_grand_lock_conflicts_with_region_locks(self):
        p = self.payload_conflict()
        p["regionTotals"]["lock"] = [300.0, 300.0]
        p["categoryTotals"]["lock"] = [None, None]
        p["grandTotal"]["lock"] = 700.0
        r = calibrate(p)
        self.assertEqual(r["status"], "infeasible")
        levels = {c["level"] for c in r["conflicts"]}
        self.assertIn("grand", levels)
        self.assertIn("region", levels)


class TestZeroAndMissingCells(unittest.TestCase):
    def test_cells_pressed_to_zero(self):
        """A category locked to zero forces all its cells to zero."""
        p = {
            "regions": ["华北", "华东"],
            "categories": ["饮料", "零食", "生鲜"],
            "cells": {"forecast": [[100, 100, 100], [100, 100, 100]],
                      "weight": [[1, 1, 1], [1, 1, 1]]},
            "regionTotals": {"forecast": [None, None], "weight": [None, None],
                             "lock": [200.0, 300.0]},
            "categoryTotals": {"forecast": [None, None, None],
                               "weight": [None, None, None],
                               "lock": [250.0, 250.0, 0.0]},
            "grandTotal": {"forecast": None, "weight": None, "lock": None},
        }
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        for i in range(2):
            self.assertAlmostEqual(r["cells"][i][2], 0.0, places=9)
        # analytic solution of the remaining system: see README derivation
        self.assertAlmostEqual(r["cells"][0][0], 100.0, places=6)
        self.assertAlmostEqual(r["cells"][0][1], 100.0, places=6)
        self.assertAlmostEqual(r["cells"][1][0], 150.0, places=6)
        self.assertAlmostEqual(r["cells"][1][1], 150.0, places=6)

    def test_missing_base_forecast_determined_by_constraints(self):
        """Cells without forecasts are still pinned down by the totals."""
        p = {
            "regions": ["华北", "华东"],
            "categories": ["饮料", "零食"],
            "cells": {"forecast": [[None, None], [None, None]],
                      "weight": [[None, None], [None, None]]},
            "regionTotals": {"forecast": [None, None], "weight": [None, None],
                             "lock": [100.0, 200.0]},
            "categoryTotals": {"forecast": [None, None], "weight": [None, None],
                               "lock": [150.0, 150.0]},
            "grandTotal": {"forecast": None, "weight": None, "lock": None},
        }
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        self.assertAlmostEqual(r["regionTotals"][0], 100.0, places=6)
        self.assertAlmostEqual(r["regionTotals"][1], 200.0, places=6)
        self.assertAlmostEqual(r["categoryTotals"][0], 150.0, places=6)
        self.assertAlmostEqual(r["categoryTotals"][1], 150.0, places=6)
        # one degree of freedom remains -> tie-break picks min norm:
        # x = [t, 100-t; 150-t, 50+t], min t^2+(100-t)^2+(150-t)^2+(50+t)^2
        # derivative: 8t - 400 = 0 -> t = 50
        self.assertAlmostEqual(r["cells"][0][0], 50.0, places=6)
        self.assertAlmostEqual(r["cells"][0][1], 50.0, places=6)
        self.assertAlmostEqual(r["cells"][1][0], 100.0, places=6)
        self.assertAlmostEqual(r["cells"][1][1], 100.0, places=6)


class TestValidation(unittest.TestCase):
    def test_weight_must_be_positive(self):
        from planner.calibrate import ValidationError
        p = payload_mismatch()
        p["cells"]["weight"][0][0] = 0
        with self.assertRaises(ValidationError):
            validate_payload(p)

    def test_dimension_mismatch_rejected(self):
        from planner.calibrate import ValidationError
        p = payload_mismatch()
        p["cells"]["forecast"] = [[1, 2], [3, 4]]
        with self.assertRaises(ValidationError):
            validate_payload(p)

    def test_negative_forecast_allowed(self):
        """Negative forecasts are legal input; non-negativity presses the
        cell to zero instead."""
        p = {
            "regions": ["甲"], "categories": ["A"],
            "cells": {"forecast": [[-50.0]], "weight": [[1.0]]},
            "regionTotals": {"forecast": [None], "weight": [None], "lock": [None]},
            "categoryTotals": {"forecast": [None], "weight": [None], "lock": [None]},
            "grandTotal": {"forecast": None, "weight": None, "lock": None},
        }
        r = calibrate(p)
        self.assertEqual(r["status"], "optimal")
        self.assertAlmostEqual(r["cells"][0][0], 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
