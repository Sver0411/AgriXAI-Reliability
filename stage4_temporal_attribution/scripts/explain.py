"""Common raw-slot mappings, explanations, and model interventions."""

import numpy as np
from scipy.stats import rankdata

from freeze_models import ANCHOR, SIGNALS

TAGS = ("60-50 min", "45-35 min", "30-20 min", "15-0 min")
TIME_GROUP = np.repeat(np.arange(4), [3, 3, 3, 4])
DIMS = ("sensor", "time", "sensor_time")
METHODS = ("TreeSHAP", "Occlusion")
EPS = 1e-9


def groups():
    slot_time, slot_sensor = np.repeat(TIME_GROUP, 4), np.tile(np.arange(4), 13)
    st = (slot_sensor[:, None] == np.arange(4)).T
    tt = (slot_time[:, None] == np.arange(4)).T
    cross = (st[:, None] & tt[None, :]).reshape(16, 52)
    names = (SIGNALS, TAGS, [f"{s} | {t}" for s in SIGNALS for t in TAGS])
    return {dimension: (list(labels), masks)
            for dimension, labels, masks in zip(DIMS, names, (st, tt, cross))}


def z_features(x):
    x = x.reshape(-1, 13, 4)
    relative = x[:, :-1, 0] - x[:, -1:, 0]
    return np.column_stack((x.reshape(-1, 52), relative))


def predict(model, x):
    return x[:, ANCHOR] + model.predict(z_features(x), num_threads=2)


def shap_features(model, x, pred):
    values = model.predict(z_features(x), pred_contrib=True, num_threads=2)
    max_error = float(np.max(abs(values.sum(axis=1) + x[:, ANCHOR] - pred)))
    if max_error > 1e-8:
        raise ValueError(f"TreeSHAP reconstruction error: {max_error}")
    return values[:, :-1], values[:, -1], max_error


def grouped_shap(phi, selectors):
    full = np.column_stack((selectors, selectors[:, np.arange(0, 48, 4)]))
    full[:, ANCHOR] = False
    return phi @ full.T, abs(phi) @ full.T


def circular_hours(times):
    return times.dt.hour.to_numpy() + times.dt.minute.to_numpy() / 60


def match_donors(x, times, background, meta, role):
    ids = np.flatnonzero(meta.role.eq(role))
    t, bg_t = circular_hours(times), circular_hours(meta.time.iloc[ids])
    dt = abs(t[:, None] - bg_t[None, :])
    clock = np.minimum(dt, 24 - dt)
    wc = abs(x[:, ANCHOR, None] - background[ids, ANCHOR])
    selected = np.argsort(wc / 3 + clock / 2, axis=1, kind="stable")[:, :2]
    rows = np.arange(len(x))[:, None]
    return ids[selected], wc[rows, selected], clock[rows, selected]


def boundary_adjustment(x, donors, use):
    """Transport a donor trajectory using only retained observed boundary levels."""
    history = x.reshape(-1, 13, 4)
    donor = donors.reshape(len(x), -1, 13, 4)
    retained = np.isfinite(history) & ~use.reshape(-1, 13, 4)
    slot = np.arange(13)[None, :, None]
    left = np.maximum.accumulate(np.where(retained, slot, -1), axis=1)
    right = np.minimum.accumulate(np.where(retained, slot, 13)[:, ::-1], axis=1)[:, ::-1]
    left = np.where(left == -1, right, left)
    right = np.where(right == 13, left, right)
    bounded = left < 13
    weight = np.divide(slot - left, right - left, out=np.zeros(left.shape), where=right > left)
    offset = np.where(retained[:, None], history[:, None] - donor, 0.0)
    lo = np.take_along_axis(offset, np.minimum(left, 12)[:, None], axis=2)
    hi = np.take_along_axis(offset, np.minimum(right, 12)[:, None], axis=2)
    amended = donor + np.where(bounded[:, None],
                               lo * (1 - weight[:, None]) + hi * weight[:, None], 0.0)
    amended[..., 0] = amended[..., 0].clip(0, 100)
    amended[..., 3] = amended[..., 3].clip(0, 100)
    return amended.reshape(donors.shape)


def replacements(model, x, donors, selector, boundary=False):
    """Keep the missingness pattern and protected origin fixed."""
    use = selector & np.isfinite(x)
    use[:, ANCHOR] = False
    candidate = boundary_adjustment(x, donors, use) if boundary else donors
    hybrid = np.where(use[:, None, :], candidate, x[:, None, :])
    return predict(model, hybrid.reshape(-1, 52)).reshape(len(x), -1), hybrid, use


def occlusion(model, x, donors, selectors, pred, boundary=False):
    signed, absolute = [], []
    for selector in selectors:
        counterfactual, _, _ = replacements(model, x, donors, selector, boundary)
        delta = pred[:, None] - counterfactual
        signed.append(delta.mean(axis=1))
        absolute.append(abs(delta).mean(axis=1))
    return np.column_stack(signed), np.column_stack(absolute)


def spearman(a, b):
    ra, rb = rankdata(a, axis=1), rankdata(b, axis=1)
    ra -= ra.mean(axis=1, keepdims=True)
    rb -= rb.mean(axis=1, keepdims=True)
    denom = np.linalg.norm(ra, axis=1) * np.linalg.norm(rb, axis=1)
    return np.divide((ra * rb).sum(axis=1), denom,
                     out=np.full(len(a), np.nan), where=denom > EPS)


def top_weights(values, k):
    cutoff = np.partition(values, -k, axis=1)[:, -k:]
    cutoff = cutoff.min(axis=1, keepdims=True)
    above, at = values > cutoff, values == cutoff
    return above + at * ((k - above.sum(axis=1, keepdims=True)) / at.sum(axis=1, keepdims=True))


def stability(signed_a, a, signed_b, b, k):
    i, j = np.triu_indices(a.shape[1], k=1)
    pa, pb = np.sign(a[:, i] - a[:, j]), np.sign(b[:, i] - b[:, j])
    denom = np.sqrt((pa != 0).sum(axis=1) * (pb != 0).sum(axis=1))
    tau = np.divide((pa * pb).sum(axis=1), denom, out=np.full(len(a), np.nan), where=denom > 0)
    l1 = (a + b).sum(axis=1)
    l2 = np.linalg.norm(a, axis=1) + np.linalg.norm(b, axis=1)
    cosine = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
    active = (abs(signed_a) > EPS) & (abs(signed_b) > EPS)
    flipped = active & (np.sign(signed_a) != np.sign(signed_b))
    return dict(
        spearman=spearman(a, b), kendall_tau_b=tau,
        topk_overlap=np.minimum(top_weights(a, k), top_weights(b, k)).sum(axis=1) / k,
        normalized_l1=np.divide(abs(a - b).sum(axis=1), l1, out=np.zeros(len(a)), where=l1 > EPS),
        normalized_l2=np.divide(np.linalg.norm(a - b, axis=1), l2, out=np.zeros(len(a)), where=l2 > EPS),
        cosine=np.divide((a * b).sum(axis=1), cosine, out=np.full(len(a), np.nan), where=cosine > EPS),
        sign_flip_rate=np.divide(flipped.sum(axis=1), active.sum(axis=1),
                                 out=np.full(len(a), np.nan), where=active.any(axis=1)),
        sign_flip_groups=flipped.sum(axis=1), jointly_active_groups=active.sum(axis=1),
    )


def donor_responses(model, x, donors, selectors, pred, jumps, boundary=False):
    effects = []
    jump_count, boundary_count = np.zeros(len(x)), np.zeros(len(x))
    for selector in selectors:
        estimate, hybrid, use = replacements(model, x, donors, selector, boundary)
        effects.append(abs(estimate - pred[:, None]).mean(axis=1))
        use = use.reshape(-1, 13, 4)
        hybrid = hybrid.reshape(len(x), donors.shape[1], 13, 4)
        at_boundary = (use[:, 1:] ^ use[:, :-1])[:, None, :, :]
        gaps = abs(np.diff(hybrid, axis=2))
        checked = at_boundary & np.isfinite(gaps)
        boundary_count += checked.sum(axis=(1, 2, 3))
        jump_count += (checked & (gaps > jumps)).sum(axis=(1, 2, 3))
    return np.column_stack(effects), jump_count, boundary_count


def trajectories(model, x, donors, selectors, pred, order, boundary=False):
    n, m = order.shape
    replaced = np.zeros_like(x, dtype=bool)
    deletion = np.zeros((n, m + 1))
    insertion = np.zeros_like(deletion)
    all_replaced, _, _ = replacements(model, x, donors, ~replaced, boundary)
    deletion[:, -1] = insertion[:, 0] = abs(all_replaced - pred[:, None]).mean(axis=1)
    for i in range(m - 1):
        replaced |= selectors[order[:, i]]
        deleted, _, _ = replacements(model, x, donors, replaced, boundary)
        inserted, _, _ = replacements(model, x, donors, ~replaced, boundary)
        deletion[:, i + 1] = abs(deleted - pred[:, None]).mean(axis=1)
        insertion[:, i + 1] = abs(inserted - pred[:, None]).mean(axis=1)
    return deletion, insertion
