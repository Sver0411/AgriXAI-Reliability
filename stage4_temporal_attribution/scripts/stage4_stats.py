"""Synchronized weekly block intervals on window-level quantities."""

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from freeze_models import SEED

N_BOOT = 2000


def block_mean(values, weeks):
    a = np.asarray(values, dtype=float)
    valid = np.isfinite(a)
    if not valid.any():
        return dict(estimate=np.nan, ci_lo=np.nan, ci_hi=np.nan, n=0, weeks=0)
    labels, unique = pd.factorize(np.asarray(weeks)[valid], sort=True)
    n_week = len(unique)
    estimate = dict(estimate=a[valid].mean(), ci_lo=np.nan, ci_hi=np.nan,
                    n=int(valid.sum()), weeks=n_week)
    if n_week < 4:
        return estimate
    sums, counts = np.bincount(labels, weights=a[valid]), np.bincount(labels)
    rng = np.random.default_rng(SEED)
    choice = rng.integers(n_week, size=(N_BOOT, n_week))
    boot = sums[choice].sum(axis=1) / counts[choice].sum(axis=1)
    estimate["ci_lo"], estimate["ci_hi"] = np.quantile(boot, [.025, .975])
    return estimate


def block_rank(x, y, weeks):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    labels, unique = pd.factorize(np.asarray(weeks)[valid], sort=True)
    result = dict(estimate=np.nan, ci_lo=np.nan, ci_hi=np.nan, n=len(x), weeks=len(unique))
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return result
    result["estimate"] = np.corrcoef(rankdata(x), rankdata(y))[0, 1]
    if len(unique) < 4:
        return result
    groups = [np.flatnonzero(labels == i) for i in range(len(unique))]
    slots = np.full((len(unique), max(map(len, groups))), -1)
    for i, group in enumerate(groups):
        slots[i, :len(group)] = group
    rng = np.random.default_rng(SEED)
    draw = rng.integers(len(unique), size=(N_BOOT, len(unique)))
    take = slots[draw].reshape(N_BOOT, -1)
    a = rankdata(np.where(take >= 0, x[take], np.nan), axis=1, nan_policy="omit")
    b = rankdata(np.where(take >= 0, y[take], np.nan), axis=1, nan_policy="omit")
    a = np.nan_to_num(a - np.nanmean(a, axis=1, keepdims=True))
    b = np.nan_to_num(b - np.nanmean(b, axis=1, keepdims=True))
    denom = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    estimable = denom > 0
    if estimable.any():
        boot = (a * b).sum(axis=1)[estimable] / denom[estimable]
        result["ci_lo"], result["ci_hi"] = np.quantile(boot, [.025, .975])
    return result
