"""End-to-end API tests: server in a thread, real HTTP via urllib."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, "/workspace")

from planner.server import make_server


def payload_mismatch():
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


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "test.db")
        cls.server = make_server("127.0.0.1", 0, cls.db)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def post(self, path, obj):
        req = urllib.request.Request(
            self.url(path), data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(self.url(path)) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))


class TestCalibrateApi(ApiTestBase):
    def test_calibrate_optimal(self):
        status, r = self.post("/api/calibrate", payload_mismatch())
        self.assertEqual(status, 200)
        self.assertEqual(r["status"], "optimal")
        self.assertAlmostEqual(sum(r["regionTotals"]), r["grandTotal"], places=6)
        self.assertAlmostEqual(sum(r["categoryTotals"]), r["grandTotal"], places=6)
        for i, row in enumerate(r["cells"]):
            self.assertAlmostEqual(sum(row), r["regionTotals"][i], places=6)

    def test_validation_error_400(self):
        p = payload_mismatch()
        p["cells"]["weight"][0][0] = -2
        status, r = self.post("/api/calibrate", p)
        self.assertEqual(status, 400)
        self.assertIn("error", r)

    def test_nan_rejected(self):
        req = urllib.request.Request(
            self.url("/api/calibrate"),
            data=b'{"regions": ["a"], "categories": ["b"], "cells": {"forecast": [[NaN]], "weight": [[1]]}}',
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                self.fail("NaN should be rejected, got %s" % body)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_infeasible_returns_conflicts(self):
        p = payload_mismatch()
        p["regionTotals"]["lock"] = [300.0, 300.0, 300.0]      # sum 900
        p["categoryTotals"]["lock"] = [400.0, 400.0, 400.0]    # sum 1200
        status, r = self.post("/api/calibrate", p)
        self.assertEqual(status, 200)
        self.assertEqual(r["status"], "infeasible")
        self.assertTrue(r["conflicts"])


class TestPublishApi(ApiTestBase):
    def test_publish_and_immutability(self):
        p = payload_mismatch()
        # publish
        status, r = self.post("/api/publish", {"name": "测试计划 v1", "input": p})
        self.assertEqual(status, 200)
        self.assertTrue(r["ok"])
        pid = r["record"]["id"]
        digest = r["record"]["sha256"]

        # the stored record matches a fresh calibration of the same input
        status, rec = self.get("/api/published/%d" % pid)
        self.assertEqual(status, 200)
        self.assertEqual(rec["name"], "测试计划 v1")
        self.assertEqual(rec["input"], p)
        self.assertEqual(rec["result"]["status"], "optimal")
        self.assertEqual(rec["sha256"], digest)
        grand_before = rec["result"]["grandTotal"]

        # list contains it
        status, lst = self.get("/api/published")
        self.assertEqual(status, 200)
        self.assertTrue(any(it["id"] == pid for it in lst["items"]))

        # now "edit the draft": calibrate something completely different
        p2 = payload_mismatch()
        p2["cells"]["forecast"][0][0] = 9999
        status, r2 = self.post("/api/calibrate", p2)
        self.assertEqual(status, 200)
        self.assertNotAlmostEqual(r2["grandTotal"], grand_before, places=3)

        # the published record must be untouched
        status, rec2 = self.get("/api/published/%d" % pid)
        self.assertEqual(rec2["result"]["grandTotal"], grand_before)
        self.assertEqual(rec2["sha256"], digest)
        self.assertEqual(rec2["input"], p)

    def test_publish_infeasible_rejected(self):
        p = payload_mismatch()
        p["regionTotals"]["lock"] = [300.0, 300.0, 300.0]      # sum 900
        p["categoryTotals"]["lock"] = [400.0, 400.0, 400.0]    # sum 1200
        status, r = self.post("/api/publish", {"name": "不可行", "input": p})
        self.assertEqual(status, 409)
        self.assertIn("calibration", r)
        self.assertEqual(r["calibration"]["status"], "infeasible")

    def test_publish_requires_name(self):
        status, r = self.post("/api/publish", {"name": "  ", "input": payload_mismatch()})
        self.assertEqual(status, 400)


class TestStaticAndSamples(ApiTestBase):
    def test_index_served(self):
        with urllib.request.urlopen(self.url("/")) as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("预测校准计划表", body)

    def test_samples_served_and_solvable(self):
        for name, expect in (("mismatch", "optimal"), ("missing_base", "optimal"),
                             ("lock_conflict", "infeasible"), ("zero_cell", "optimal")):
            status, sample = self.get("/samples/%s.json" % name)
            self.assertEqual(status, 200)
            status, r = self.post("/api/calibrate", sample["input"])
            self.assertEqual(status, 200)
            self.assertEqual(r["status"], expect, "%s -> %s" % (name, r.get("status")))


if __name__ == "__main__":
    unittest.main()
