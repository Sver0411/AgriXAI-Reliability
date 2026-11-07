
"""Profile original availability and apply paired, equal-budget test masks."""

import numpy as np
import pandas as pd

from prepare_data import SIGNALS, MINUTE, clock_ns

SEED = 20261107


def run_lengths(mask):
    changes = np.diff(np.r_[False, mask, False].astype(np.int8))
    return np.flatnonzero(changes == -1) - np.flatnonzero(changes == 1)


def availability(meta, calendar, slots, local, air):
    rows, hours = [], []
    native = meta["native_minutes"]
    missing = ~np.isfinite(slots)
    key = {col: meta[col] for col in ("dataset", "group", "split", "unit", "channel")}
    for i, signal in enumerate(SIGNALS):
        source = local if i < 2 else air
        source = source.loc[calendar[0] - pd.Timedelta(minutes=native):calendar[-1], signal]
        for basis, times in (
            ("raw_rows", source.index),
            ("finite_observations", source.index[np.isfinite(source.to_numpy(dtype=float))]),
        ):
            intervals = np.diff(clock_ns(times)) / MINUTE
            if not len(intervals):
                continue
            rows.append(key | dict(
                signal=signal, basis=basis, rows=len(times),
                interval_p05_min=np.quantile(intervals, .05),
                interval_median_min=np.median(intervals),
                interval_p95_min=np.quantile(intervals, .95),
                interval_max_min=intervals.max(),
                intervals_lt_half_cadence=int((intervals < native / 2).sum()),
                intervals_gt_1_5_cadence=int((intervals > native * 1.5).sum()),
                intervals_gt_60_min=int((intervals > 60).sum()),
            ))
        support = np.flatnonzero(~missing[:, i])
        if not len(support):
            continue
        s = slice(support[0], support[-1] + 1)
        gap = missing[s, i]
        duration = run_lengths(gap)
        others = missing[s].sum(axis=1) - gap
        rows.append(key | dict(
            signal=signal, basis="model_slots_inside_signal_support", rows=len(gap),
            missing_slots=int(gap.sum()), single_runs=int((duration == 1).sum()),
            multi_runs=int((duration >= 2).sum()),
            runs_ge_60min=int((duration * native >= 60).sum()),
            max_run_min=int(duration.max(initial=0) * native),
            only_signal_missing_slots=int((gap & (others == 0)).sum()),
            other_signal_also_missing_slots=int((gap & (others > 0)).sum()),
            local_device_both_missing_slots=int(missing[s, :2].all(axis=1).sum()),
            all_four_missing_slots=int(missing[s].all(axis=1).sum()),
        ))
        for hour in range(24):
            at_hour = calendar[s].hour == hour
            hours.append(key | dict(signal=signal, export_hour=hour,
                                    slots=int(at_hour.sum()), missing=int((gap & at_hour).sum())))
    return rows, hours


def sensor_coincidence(dataset, series):
    rows = []
    for group, channel_series in series.items():
        frame = pd.concat(channel_series, axis=1, join="inner")
        absent = frame.to_numpy(dtype=bool)
        n_missing = absent.sum(axis=1)
        overlap = dict(dataset=dataset, group=group, n_channels=absent.shape[1],
                       common_slots=len(frame), exactly_one_missing=int((n_missing == 1).sum()),
                       multiple_missing=int((n_missing >= 2).sum()),
                       all_missing=int((n_missing == absent.shape[1]).sum()))
        rows.append(overlap)
    return rows


def paired_masks(x, frame, bank):
    """Each test input window is disjoint from the next in its own channel."""
    chosen = []
    for _, sub in frame.groupby(["group", "unit", "channel"], sort=True):
        previous = None
        for row in sub.sort_values("time").itertuples():
            if previous is None or row.time - previous > pd.Timedelta(minutes=60):
                chosen.append(row.Index)
                previous = row.time
    x = x[chosen]
    test = frame.iloc[chosen].reset_index(drop=True)
    assert np.isfinite(x).all()
    rng = np.random.default_rng(SEED)
    structured = bank[rng.integers(len(bank), size=len(test))]
    random = np.zeros_like(structured)
    for feature in range(x.shape[2]):
        choices = x.shape[1] - int(feature == 0)
        # Ranks give uniformly drawn distinct positions for each per-signal budget.
        draws = rng.random((len(test), choices))
        ranks = np.argsort(np.argsort(draws, axis=1), axis=1)
        random[:, :choices, feature] = ranks < structured[:, :, feature].sum(axis=1)[:, None]
    assert not structured[:, -1, 0].any() and not random[:, -1, 0].any()
    np.testing.assert_array_equal(structured.sum(axis=1), random.sum(axis=1))
    x_structured = np.where(structured, np.nan, x)
    x_random = np.where(random, np.nan, x)
    return test, (x, x_structured, x_random), (structured, random)


def mask_totals(dataset, test, masks, n_train_masks):
    rows = []
    groups = [("pooled", test.index)]
    groups.extend((unit, sub.index) for unit, sub in test.groupby("unit", sort=True))
    for scope, idx in groups:
        for condition, mask in zip(("record_derived", "matched_random"), masks):
            subset = mask[idx]
            for i, feature in enumerate(SIGNALS):
                m = subset[:, :, i]
                rows.append(dict(
                    dataset=dataset, test_scope=scope, condition=condition, signal=feature,
                    windows=len(subset), slots=m.size, masked=int(m.sum()),
                    missing_rate=m.mean(), windows_with_adjacent_missing=int(
                        (m[:, :-1] & m[:, 1:]).any(axis=1).sum()
                    ),
                    windows_with_any_synchronous_missing=int(
                        (subset.sum(axis=2) >= 2).any(axis=1).sum()
                    ),
                    training_candidate_masks=n_train_masks, seed=SEED,
                ))
    return rows
