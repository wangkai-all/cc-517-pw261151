"""Forecast reconciliation: merge region-level and category-level forecasts
into one consistent region-by-category plan.

Model
-----
Variables: x[r][c] >= 0, one per region-category cell.
Aggregates (region totals, category totals, grand total) are plain sums of
the cells.  Every provided forecast contributes a term  w * (agg(x) - f)^2
to the objective; forecasts that were not provided contribute nothing (they
are NOT treated as zero).  Locked aggregates become equality constraints.

The solve is lexicographic:
  stage 1: minimise the weighted squared deviation F(x);
  stage 2: among all minimisers of F, pick the one minimising sum(x^2),
           so identical inputs always yield the identical plan.

Feasibility conflicts (locked region totals vs locked category totals vs
non-negativity) are reported as an irreducible infeasible subsystem (IIS)
of locks, computed with a deletion filter over a simplex feasibility oracle.
"""

from .linalg import dot
from .qp import solve_qp, find_feasible, InfeasibleError, SolverError


class ValidationError(Exception):
    """The request payload is invalid (client error)."""


# ---------------------------------------------------------------------------
# payload validation
# ---------------------------------------------------------------------------

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _opt_num(v, what):
    if v is None:
        return None
    if not _is_num(v):
        raise ValidationError("%s 必须是数字或空值" % what)
    return float(v)


def _names(v, what):
    if not isinstance(v, list) or not v:
        raise ValidationError("%s不能为空" % what)
    out = []
    for item in v:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError("%s含有非法名称" % what)
        out.append(item.strip())
    if len(set(out)) != len(out):
        raise ValidationError("%s名称重复" % what)
    return out


def _matrix(v, nr, nc, what):
    if not isinstance(v, list) or len(v) != nr:
        raise ValidationError("%s行数应为 %d" % (what, nr))
    out = []
    for row in v:
        if not isinstance(row, list) or len(row) != nc:
            raise ValidationError("%s每行应有 %d 列" % (what, nc))
        out.append([_opt_num(x, what) for x in row])
    return out


def _vector(v, n, what):
    if not isinstance(v, list) or len(v) != n:
        raise ValidationError("%s长度应为 %d" % (what, n))
    return [_opt_num(x, what) for x in v]


def validate_payload(p):
    """Validate the request payload; return a normalised copy.

    Raises ValidationError with a human-readable (Chinese) message.
    """
    if not isinstance(p, dict):
        raise ValidationError("请求体必须是 JSON 对象")
    regions = _names(p.get("regions"), "地区列表")
    categories = _names(p.get("categories"), "品类列表")
    nr, nc = len(regions), len(categories)

    cells = p.get("cells") or {}
    cell_f = _matrix(cells.get("forecast") or [[None] * nc for _ in range(nr)], nr, nc, "单元格预测")
    cell_w = _matrix(cells.get("weight") or [[None] * nc for _ in range(nr)], nr, nc, "单元格权重")

    rt = p.get("regionTotals") or {}
    reg_f = _vector(rt.get("forecast") or [None] * nr, nr, "地区总量预测")
    reg_w = _vector(rt.get("weight") or [None] * nr, nr, "地区总量权重")
    reg_l = _vector(rt.get("lock") or [None] * nr, nr, "地区总量锁定")

    ct = p.get("categoryTotals") or {}
    cat_f = _vector(ct.get("forecast") or [None] * nc, nc, "品类总量预测")
    cat_w = _vector(ct.get("weight") or [None] * nc, nc, "品类总量权重")
    cat_l = _vector(ct.get("lock") or [None] * nc, nc, "品类总量锁定")

    gt = p.get("grandTotal") or {}
    grd_f = _opt_num(gt.get("forecast"), "全盘总量预测")
    grd_w = _opt_num(gt.get("weight"), "全盘总量权重")
    grd_l = _opt_num(gt.get("lock"), "全盘总量锁定")

    def check_weight(f, w, what):
        if f is not None and (w is None or w <= 0):
            raise ValidationError("%s提供了预测值，权重必须为正数" % what)

    for i in range(nr):
        for j in range(nc):
            check_weight(cell_f[i][j], cell_w[i][j], "单元格(%s×%s)" % (regions[i], categories[j]))
    for i in range(nr):
        check_weight(reg_f[i], reg_w[i], "地区总量(%s)" % regions[i])
    for j in range(nc):
        check_weight(cat_f[j], cat_w[j], "品类总量(%s)" % categories[j])
    check_weight(grd_f, grd_w, "全盘总量")

    return {
        "regions": regions,
        "categories": categories,
        "cellForecast": cell_f, "cellWeight": cell_w,
        "regionForecast": reg_f, "regionWeight": reg_w, "regionLock": reg_l,
        "categoryForecast": cat_f, "categoryWeight": cat_w, "categoryLock": cat_l,
        "grandForecast": grd_f, "grandWeight": grd_w, "grandLock": grd_l,
    }


# ---------------------------------------------------------------------------
# problem construction
# ---------------------------------------------------------------------------

def _dense(row_sparse, n):
    row = [0.0] * n
    for k, v in row_sparse.items():
        row[k] = v
    return row


def build_problem(spec):
    """Turn a validated spec into objective terms and lock constraints."""
    nr = len(spec["regions"])
    nc = len(spec["categories"])
    n = nr * nc

    def cell_row(i, j):
        return {i * nc + j: 1.0}

    def region_row(i):
        return {i * nc + j: 1.0 for j in range(nc)}

    def category_row(j):
        return {i * nc + j: 1.0 for i in range(nr)}

    def grand_row():
        return {k: 1.0 for k in range(n)}

    terms = []   # each: dict(level,label,row(sparse),forecast,weight)
    locks = []   # each: dict(level,index,label,row(sparse),value)

    for i in range(nr):
        for j in range(nc):
            f = spec["cellForecast"][i][j]
            if f is not None:
                terms.append({
                    "level": "cell",
                    "label": "%s×%s" % (spec["regions"][i], spec["categories"][j]),
                    "row": cell_row(i, j), "forecast": f,
                    "weight": spec["cellWeight"][i][j],
                })
    for i in range(nr):
        f = spec["regionForecast"][i]
        if f is not None:
            terms.append({
                "level": "region", "label": spec["regions"][i],
                "row": region_row(i), "forecast": f,
                "weight": spec["regionWeight"][i],
            })
        v = spec["regionLock"][i]
        if v is not None:
            locks.append({
                "level": "region", "index": i, "label": spec["regions"][i],
                "row": region_row(i), "value": v,
            })
    for j in range(nc):
        f = spec["categoryForecast"][j]
        if f is not None:
            terms.append({
                "level": "category", "label": spec["categories"][j],
                "row": category_row(j), "forecast": f,
                "weight": spec["categoryWeight"][j],
            })
        v = spec["categoryLock"][j]
        if v is not None:
            locks.append({
                "level": "category", "index": j, "label": spec["categories"][j],
                "row": category_row(j), "value": v,
            })
    if spec["grandForecast"] is not None:
        terms.append({
            "level": "grand", "label": "全盘",
            "row": grand_row(), "forecast": spec["grandForecast"],
            "weight": spec["grandWeight"],
        })
    if spec["grandLock"] is not None:
        locks.append({
            "level": "grand", "index": None, "label": "全盘",
            "row": grand_row(), "value": spec["grandLock"],
        })

    # dense quadratic form of the objective F(x) = 0.5 x'Hx + c'x + const
    H = [[0.0] * n for _ in range(n)]
    c = [0.0] * n
    for t in terms:
        w = t["weight"]
        items = list(t["row"].items())
        for a, va in items:
            wva = 2.0 * w * va
            Ha = H[a]
            for b, vb in items:
                Ha[b] += wva * vb
            c[a] -= wva * t["forecast"]

    Aeq = [_dense(l["row"], n) for l in locks]
    beq = [l["value"] for l in locks]
    return {"nr": nr, "nc": nc, "n": n, "terms": terms, "locks": locks,
            "H": H, "c": c, "Aeq": Aeq, "beq": beq}


def evaluate(terms, x):
    """Recompute the objective from its definition, with a per-term breakdown."""
    total = 0.0
    breakdown = []
    for t in terms:
        value = sum(coef * x[k] for k, coef in t["row"].items())
        dev = value - t["forecast"]
        contrib = t["weight"] * dev * dev
        total += contrib
        breakdown.append({
            "level": t["level"], "label": t["label"],
            "forecast": t["forecast"], "weight": t["weight"],
            "value": value, "deviation": dev, "contribution": contrib,
        })
    return total, breakdown


def _row_basis(rows, n, tol=1e-9):
    """Row basis (echelon form) of the sparse rows; len == rank of the matrix."""
    basis = []       # list of dense rows
    pivots = []      # pivot column of each basis row
    for sparse in rows:
        r = _dense(sparse, n)
        for b, p in zip(basis, pivots):
            f = r[p]
            if f != 0.0:
                r = [a - f * bv for a, bv in zip(r, b)]
        rmax = max((abs(v) for v in r), default=0.0)
        piv = -1
        if rmax > 1e-9:
            for j, v in enumerate(r):
                if abs(v) > 1e-9 * rmax:
                    piv = j
                    break
        if piv >= 0:
            inv = 1.0 / r[piv]
            basis.append([v * inv for v in r])
            pivots.append(piv)
    return basis


# ---------------------------------------------------------------------------
# conflict analysis (IIS)
# ---------------------------------------------------------------------------

def compute_iis(locks, n):
    """Deletion filter: return an irreducible infeasible subsystem of locks."""
    idxs = list(range(len(locks)))

    def feasible(sub):
        Aeq = [_dense(locks[i]["row"], n) for i in sub]
        beq = [locks[i]["value"] for i in sub]
        return find_feasible(Aeq, beq) is not None

    for i in list(idxs):
        trial = [j for j in idxs if j != i]
        if not feasible(trial):
            idxs = trial
    return [locks[i] for i in idxs]


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------

def calibrate(payload):
    """Validate, solve and verify.  Returns a JSON-serialisable result dict."""
    spec = validate_payload(payload)
    prob = build_problem(spec)
    n, nr, nc = prob["n"], prob["nr"], prob["nc"]
    Aeq, beq = prob["Aeq"], prob["beq"]

    # --- feasibility first: never optimise over an empty set ---------------
    if Aeq and find_feasible(Aeq, beq) is None:
        iis = compute_iis(prob["locks"], n)
        return {
            "status": "infeasible",
            "message": "锁定条件互相冲突（或与非负约束冲突），计划不可行，不能发布。",
            "conflicts": [
                {"level": l["level"], "index": l["index"],
                 "label": l["label"], "value": l["value"]}
                for l in iis
            ],
        }

    # --- stage 1: minimise the weighted squared deviation ------------------
    try:
        x1, info1 = solve_qp(prob["H"], prob["c"], Aeq, beq)
    except InfeasibleError:  # pragma: no cover - guarded above, defensive
        return {"status": "infeasible", "message": "计划不可行。", "conflicts": []}
    except SolverError as exc:
        return {"status": "solver_error", "message": "求解器失败：%s" % exc}
    v1, _ = evaluate(prob["terms"], x1)

    # --- stage 2: among minimisers of F, minimise sum(x^2) -----------------
    # The optimal face of stage 1 is polyhedral:
    #   {x >= 0 : Aeq x = beq, H x = H x1}
    # (the gradient condition g'(x - x1) = 0 is implied, because c lies in
    # row(H) and row(H) is orthogonal to null(H)).  Since null(H) equals
    # null(A) for the terms matrix A, H x = H x1 is equivalent to B x = B x1
    # with B any row basis of A.  If A has full row rank the stage-1
    # optimum is unique and stage 2 is skipped entirely.
    basis = _row_basis([t["row"] for t in prob["terms"]], n)
    if len(basis) >= n:
        x2, info2 = x1, {"skipped": "unique optimum (full rank)"}
    else:
        A2 = [row[:] for row in Aeq] + basis
        b2 = list(beq) + [dot(b, x1) for b in basis]
        H2 = [[2.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
        c2 = [0.0] * n
        try:
            x2, info2 = solve_qp(H2, c2, A2, b2, x0=x1)
        except (InfeasibleError, SolverError):
            # Numerically the face system should always be feasible (x1 lies
            # on it); if the linear algebra still fails, fall back to the
            # stage-1 optimum rather than returning nothing.
            x2, info2 = x1, {"fallback": True}

    # --- verification: recompute everything from the definition ------------
    v2, breakdown = evaluate(prob["terms"], x2)
    cells = [[x2[i * nc + j] for j in range(nc)] for i in range(nr)]
    region_totals = [sum(cells[i]) for i in range(nr)]
    category_totals = [sum(cells[i][j] for i in range(nr)) for j in range(nc)]
    grand_total = sum(region_totals)

    lock_checks = []
    max_lock_res = 0.0
    for l in prob["locks"]:
        actual = sum(coef * x2[k] for k, coef in l["row"].items())
        res = abs(actual - l["value"])
        max_lock_res = max(max_lock_res, res)
        lock_checks.append({
            "level": l["level"], "index": l["index"], "label": l["label"],
            "value": l["value"], "actual": actual, "residual": res,
        })
    min_cell = min((v for row in cells for v in row), default=0.0)
    scale = max(1.0, abs(grand_total),
                max((abs(l["value"]) for l in prob["locks"]), default=0.0))

    problems = []
    if max_lock_res > 1e-6 * scale:
        problems.append("锁定残差过大：%.3e" % max_lock_res)
    if min_cell < -1e-7 * scale:
        problems.append("出现负的单元格：%.3e" % min_cell)
    if v2 > v1 + 1e-6 * max(1.0, abs(v1)):
        problems.append("第二阶段目标值 %.6f 差于第一阶段 %.6f" % (v2, v1))
    if problems:
        return {
            "status": "solver_error",
            "message": "求解结果未通过校验：" + "；".join(problems),
        }

    return {
        "status": "optimal",
        "cells": cells,
        "regionTotals": region_totals,
        "categoryTotals": category_totals,
        "grandTotal": grand_total,
        "objective": v2,
        "objectiveTerms": breakdown,
        "locks": lock_checks,
        "checks": {
            "maxLockResidual": max_lock_res,
            "minCellValue": min_cell,
            "objectiveStage1": v1,
            "objectiveFinal": v2,
            "tieBreakSumSquares": dot(x2, x2),
            "stage1": info1,
            "stage2": info2,
        },
    }
