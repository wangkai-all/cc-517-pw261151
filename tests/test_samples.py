"""Verify the bundled sample datasets behave exactly as documented."""
import json
import os
import sys
import unittest

sys.path.insert(0, "/workspace")

from planner.calibrate import calibrate

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")


def load(name):
    with open(os.path.join(SAMPLES, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


class TestSamples(unittest.TestCase):
    def test_mismatch_reconciles(self):
        """Region forecasts (1140), category forecasts (1120), grand (1150)
        and cells (1080) disagree; the calibrated plan must be consistent."""
        s = load("mismatch")
        r = calibrate(s["input"])
        self.assertEqual(r["status"], "optimal")
        self.assertAlmostEqual(sum(r["regionTotals"]), r["grandTotal"], places=6)
        self.assertAlmostEqual(sum(r["categoryTotals"]), r["grandTotal"], places=6)
        # the reconciled grand total sits between the disagreeing forecasts
        self.assertTrue(1080 <= r["grandTotal"] <= 1150)
        # every provided forecast participates: 9 cells + 3 + 3 + 1
        self.assertEqual(len(r["objectiveTerms"]), 16)

    def test_missing_base_determined(self):
        s = load("missing_base")
        r = calibrate(s["input"])
        self.assertEqual(r["status"], "optimal")
        # the locked region total holds exactly
        self.assertAlmostEqual(r["regionTotals"][0], 300.0, places=6)
        # missing cells got concrete non-negative values
        self.assertGreaterEqual(r["cells"][1][1], 0.0)
        self.assertGreaterEqual(r["cells"][2][0], 0.0)
        self.assertGreaterEqual(r["cells"][2][2], 0.0)
        # missing cells do not appear in the objective
        labels = [t["label"] for t in r["objectiveTerms"]]
        self.assertNotIn("华东×零食", labels)
        self.assertNotIn("华南×饮料", labels)
        self.assertNotIn("华南×生鲜", labels)
        # 6 provided cells + 3 regions + 3 categories + 1 grand
        self.assertEqual(len(r["objectiveTerms"]), 13)

    def test_lock_conflict_iis(self):
        s = load("lock_conflict")
        r = calibrate(s["input"])
        self.assertEqual(r["status"], "infeasible")
        self.assertEqual(len(r["conflicts"]), 4)
        # removing any one of them restores feasibility
        for c in r["conflicts"]:
            inp = json.loads(json.dumps(s["input"]))
            if c["level"] == "region":
                inp["regionTotals"]["lock"][c["index"]] = None
            else:
                inp["categoryTotals"]["lock"][c["index"]] = None
            r2 = calibrate(inp)
            self.assertEqual(r2["status"], "optimal")

    def test_zero_cell(self):
        s = load("zero_cell")
        r = calibrate(s["input"])
        self.assertEqual(r["status"], "optimal")
        # 生鲜 locked to zero -> both cells exactly zero
        self.assertAlmostEqual(r["cells"][0][2], 0.0, places=9)
        self.assertAlmostEqual(r["cells"][1][2], 0.0, places=9)
        # analytic solution for the remaining cells
        self.assertAlmostEqual(r["cells"][0][0], 100.0, places=6)
        self.assertAlmostEqual(r["cells"][0][1], 100.0, places=6)
        self.assertAlmostEqual(r["cells"][1][0], 150.0, places=6)
        self.assertAlmostEqual(r["cells"][1][1], 150.0, places=6)
        self.assertAlmostEqual(r["grandTotal"], 500.0, places=6)


if __name__ == "__main__":
    unittest.main()
