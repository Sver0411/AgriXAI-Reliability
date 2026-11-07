
"""Run the frozen Stage 3 protocol. The only writes are result CSVs in the output directory."""

import argparse
from itertools import chain
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from missingness import SEED, availability, mask_totals, paired_masks, sensor_coincidence
from prepare_data import HORIZONS, OUT, SIGNALS, build_channel, parma, verify_sources, wur

MODELS = ("Persistence", "Ridge", "LightGBM")
PRIMARY = 60
BOOTSTRAPS = 2000


def features(history):
    n, steps, _ = history.shape
    relative = history[:, :, 0] - history[:, -1:, 0]
    values = np.column_stack([history.reshape(n, -1), relative[:, :-1]])
    columns = [f"{signal}_{i}" for i in range(steps) for signal in SIGNALS]
    columns += [f"moisture_relative_{i}" for i in range(steps - 1)]
    return pd.DataFrame(values, columns=columns)


def interval(week, delta):
    codes, labels = pd.factorize(week, sort=True)
    count = np.bincount(codes)
    total = np.bincount(codes, weights=delta)
    if len(labels) < 2:
        return len(labels), np.nan, np.nan
    rng = np.random.default_rng(SEED)
    draw = rng.integers(len(labels), size=(BOOTSTRAPS, len(labels)))
    distribution = total[draw].sum(axis=1) / count[draw].sum(axis=1)
    lo, hi = np.quantile(distribution, [.025, .975])
    return len(labels), lo, hi


def scores(frame, pred, persist):
    truth = frame.truth.to_numpy()
    error = np.abs(truth - pred)
    reference = np.abs(truth - persist)
    mae = error.mean()
    spread = ((truth - truth.mean()) ** 2).sum()
    squared = ((truth - pred) ** 2).sum()
    weeks, lo, hi = interval(frame.week, reference - error)
    return dict(
        n=len(frame), mae=mae, rmse=np.sqrt(squared / len(frame)),
        r2=1 - squared / spread if spread else np.nan,
        persistence_mae=reference.mean(),
        mae_skill=1 - mae / reference.mean() if reference.mean() else np.nan,
        mae_gain=reference.mean() - mae, weeks=weeks, gain_ci_lo=lo, gain_ci_hi=hi,
        label_offset_p05_min=frame.label_offset_min.quantile(.05),
        label_offset_median_min=frame.label_offset_min.median(),
        label_offset_p95_min=frame.label_offset_min.quantile(.95),
        label_offset_max_abs_min=frame.label_offset_min.abs().max(),
    )


def fit(history, frame):
    x = features(history)
    current = history[:, -1, 0]
    target = frame.truth.to_numpy() - current
    train = frame.split == "train"
    val = frame.split == "validation"
    estimators = {
        "Ridge": [
            (f"alpha={alpha}", make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                             Ridge(alpha=alpha))) for alpha in (.1, 1., 10.)
        ],
        "LightGBM": [
            (f"num_leaves={leaves}", LGBMRegressor(
                num_leaves=leaves, n_estimators=220, learning_rate=.05, min_child_samples=100,
                random_state=SEED, n_jobs=2, deterministic=True, force_col_wise=True, verbosity=-1,
            )) for leaves in (15, 31)
        ],
    }
    fitted, parameters, validation = {}, {}, {}
    for family, candidates in estimators.items():
        best = float("inf")
        for name, candidate in candidates:
            candidate.fit(x.loc[train], target[train])
            loss = np.abs(candidate.predict(x.loc[val]) - target[val]).mean()
            if loss < best:
                best = loss
                fitted[family] = candidate
                parameters[family] = name
        validation[family] = best
    selected = min(validation, key=validation.get)
    predictions = {"Persistence": current}
    predictions.update({name: current + estimator.predict(x) for name, estimator in fitted.items()})
    return fitted, predictions, parameters, selected


def grouped(frame, dataset):
    for split, part in frame.groupby("split", sort=True):
        for group, sub in part.groupby("group", sort=True):
            yield split, "season" if dataset == "Parma" else "compartment", group, "", "", sub
            if dataset == "Parma":
                for unit, line in sub.groupby("unit", sort=True):
                    yield split, "line", group, unit, "", line
            for (unit, channel), channel_frame in sub.groupby(["unit", "channel"], sort=True):
                level = "probe" if dataset == "Parma" else "slab"
                yield split, level, group, unit, channel, channel_frame


def missingness_results(dataset, model, estimator, test, inputs, selected):
    predictions = []
    for history in inputs:
        current = history[:, -1, 0]
        pred = current if estimator is None else current + estimator.predict(features(history))
        predictions.append(pred)
    errors = np.abs(np.asarray(predictions) - test.truth.to_numpy())
    comparisons = (
        ("record_minus_complete", 1, 0), ("random_minus_complete", 2, 0),
        ("record_minus_random", 1, 2),
    )
    groups = [("pooled", test.index)]
    groups.extend((unit, sub.index) for unit, sub in test.groupby("unit", sort=True))
    rows = []
    for scope, idx in groups:
        for name, a, b in comparisons:
            delta = errors[a, idx] - errors[b, idx]
            weeks, lo, hi = interval(test.loc[idx, "week"], delta)
            rows.append(dict(
                dataset=dataset, scope=scope, horizon_min=PRIMARY, model=model,
                selected_by_validation=model == selected, comparison=name, n=len(idx), weeks=weeks,
                mae_complete=errors[0, idx].mean(), mae_record=errors[1, idx].mean(),
                mae_random=errors[2, idx].mean(), delta_mae=delta.mean(), ci_lo=lo, ci_hi=hi,
                seed=SEED,
            ))
    return rows


def run_domain(source, results):
    windows = {h: [] for h in HORIZONS}
    bank = []
    streams = {}
    for meta, local, air in source():
        examples, manifest, masks, native = build_channel(meta, local, air)
        results["split_manifest"].append(manifest)
        observed, hourly = availability(meta, *native)
        results["observed_missingness"].extend(observed)
        results["missingness_by_hour"].extend(hourly)
        calendar, slots, _, _ = native
        streams.setdefault(meta["group"], []).append(
            pd.Series(~np.isfinite(slots[:, 0]), index=calendar, name=meta["channel"])
        )
        if meta["split"] == "train":
            bank.append(masks)
        for h, example in examples.items():
            windows[h].append(example)
    dataset = meta["dataset"]
    results["sensor_gap_coincidence"].extend(sensor_coincidence(dataset, streams))
    bank = np.concatenate(bank)
    assert len(bank) > 0
    for horizon, examples in windows.items():
        history = np.concatenate([x for x, _ in examples])
        frame = pd.concat([f for _, f in examples], ignore_index=True)
        assert frame.groupby("group").split.nunique().eq(1).all()
        assert not frame.duplicated(["group", "unit", "channel", "time"]).any()
        assert set(frame.split) == {"train", "validation", "test"}
        if dataset == "Parma":
            times = frame.groupby("split").time.agg(["min", "max"])
            assert times.loc["train", "max"] < times.loc["validation", "min"]
            assert times.loc["validation", "max"] < times.loc["test", "min"]
        print(dataset, horizon, frame.split.value_counts().to_dict(), flush=True)
        fitted, preds, params, selected = fit(history, frame)
        persist = preds["Persistence"]
        for model in MODELS:
            pred = preds[model]
            assert np.isfinite(pred).all()
            meta = dict(dataset=dataset, horizon_min=horizon, model=model,
                        parameters=params.get(model, ""), selected_by_validation=model == selected)
            for split, sub in frame.groupby("split", sort=True):
                row = meta | dict(split=split) | scores(sub, pred[sub.index], persist[sub.index])
                results["baseline_metrics"].append(row)
                if split == "test":
                    val = frame.split == "validation"
                    results["prediction_horizon_comparison"].append(row | dict(
                        validation_mae=np.abs(pred[val] - frame.loc[val, "truth"]).mean(),
                        primary_horizon=horizon == PRIMARY,
                    ))
            for split, level, group, unit, channel, sub in grouped(frame, dataset):
                row = meta | dict(split=split, level=level, group=group, unit=unit, channel=channel)
                results["per_group_metrics"].append(row | scores(sub, pred[sub.index], persist[sub.index]))
        if horizon == PRIMARY:
            mask = frame.split == "test"
            test, inputs, masks = paired_masks(history[mask], frame.loc[mask].reset_index(drop=True), bank)
            results["missingness_mask_totals"].extend(mask_totals(dataset, test, masks, len(bank)))
            for model in MODELS:
                results["missingness_prediction_metrics"].extend(
                    missingness_results(dataset, model, fitted.get(model), test, inputs, selected)
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT, help="Directory for generated result CSVs")
    args = parser.parse_args()
    verify_sources()
    names = (
        "split_manifest", "baseline_metrics", "per_group_metrics", "prediction_horizon_comparison",
        "observed_missingness", "missingness_by_hour", "sensor_gap_coincidence",
        "missingness_mask_totals", "missingness_prediction_metrics",
    )
    results = {name: [] for name in names}
    for source in (parma, wur):
        run_domain(source, results)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, rows in results.items():
        pd.DataFrame(rows).to_csv(args.output / f"{name}.csv", index=False, float_format="%.9g")
    print(f"Results written to {args.output}", flush=True)


if __name__ == "__main__":
    main()
