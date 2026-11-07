"""Run the preregistered attribution and intervention experiments on frozen boosters."""

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from explain import (DIMS, METHODS, donor_responses, grouped_shap, groups, match_donors,
                     occlusion, predict, shap_features, spearman, stability, trajectories)
from freeze_models import ANCHOR, HORIZONS, ROOT, SEED, csv, json_write, sha
from stage4_stats import block_mean

CONDS_FAITH = (0, 1, 2)
EVAL_METRICS = ("deletion_auc", "insertion_auc", "comprehensiveness", "sufficiency")
EXPLAIN = groups()


def summarize_attributes(h, conditions, compartments, signed, absolute, names, method):
    n, m = len(compartments), len(names)
    signed = signed.reshape(len(conditions), n, m)
    absolute = absolute.reshape(len(conditions), n, m)
    rows = []
    for condition in dict.fromkeys(conditions):
        chosen = conditions == condition
        s, a = signed[chosen].mean(axis=0), absolute[chosen].mean(axis=0)
        for scope in ("pooled", *sorted(set(compartments))):
            keep = np.ones(n, dtype=bool) if scope == "pooled" else compartments == scope
            level_sum = a[keep].sum()
            for i, label in enumerate(names):
                values = s[keep, i]
                rows.append(dict(
                    horizon_min=h, condition=condition, scope=scope, method=method, group_label=label,
                    n=int(keep.sum()), repeats=int(chosen.sum()), signed_mean=values.mean(),
                    absolute_mean=a[keep, i].mean(), share_of_absolute=a[keep, i].sum() / level_sum
                    if level_sum else np.nan, positive_fraction=(values > 1e-9).mean(),
                    negative_fraction=(values < -1e-9).mean(),
                ))
    return rows


def window_stability(h, conditions, reps, sample, masks, pred, signed, absolute, method, dim):
    c, n = len(conditions), len(sample)
    signed = signed.reshape(c, n, -1)
    absolute = absolute.reshape(c, n, -1)
    pred = pred.reshape(c, n)
    k = 4 if dim == "sensor_time" else 2
    y = sample[f"truth_{h}"].to_numpy()
    rows = []
    for i in range(1, c):
        delta = pred[i] - pred[0]
        rows.append(pd.DataFrame(dict(
            horizon_min=h, method=method, dimension=dim, condition=conditions[i], repeat=reps[i],
            sample_id=sample.sample_id, compartment=sample.group, slab=sample.channel, week=sample.week,
            masked=masks[i].sum(axis=(1, 2)), pred_complete=pred[0], pred_perturbed=pred[i],
            truth=y, prediction_delta=delta, abs_prediction_delta=abs(delta),
            abs_error_delta=abs(pred[i] - y) - abs(pred[0] - y),
        ) | stability(signed[0], absolute[0], signed[i], absolute[i], k)))
    return pd.concat(rows, ignore_index=True)


def curve_rows(meta, sample, deletion, insertion):
    rows = []
    for scope in ("pooled", *sorted(sample.group.unique())):
        keep = sample.group.eq(scope) if scope != "pooled" else np.ones(len(sample), dtype=bool)
        for name, curve in (("deletion", deletion), ("insertion", insertion)):
            for step in range(curve.shape[1]):
                stat = block_mean(curve[keep, step], sample.loc[keep, "week"])
                rows.append(meta | dict(scope=scope, experiment=name, step=step,
                                        fraction=step / (curve.shape[1] - 1)) | stat)
    return rows


def faithfulness(h, model, masked, yhat, sample, conds, donors, background, scores, boundary=False, dims=DIMS):
    n = len(sample)
    rows, curves, diagnostic = [], [], []
    q99 = np.quantile(abs(np.diff(background.reshape(-1, 13, 4), axis=1)), .99, axis=(0, 1))
    rng = np.random.default_rng(SEED)
    for ci in CONDS_FAITH:
        x, pred = masked[ci], yhat[ci]
        for dim in dims:
            _, selectors = EXPLAIN[dim]
            effect, n_jump, n_boundary = donor_responses(model, x, donors, selectors, pred, q99, boundary)
            mutable = selectors.copy()
            mutable[:, ANCHOR] = False
            untestable = (~(np.isfinite(x) @ mutable.T)).mean(axis=1)
            if dim == "sensor_time":
                diagnostic.append(pd.DataFrame(dict(
                    horizon_min=h, condition=conds[ci], sample_id=sample.sample_id,
                    compartment=sample.group, week=sample.week, boundary_n=n_boundary,
                    jump_n=n_jump, untestable_group_fraction=untestable,
                )))
            rankings = rng.random((5, n, len(selectors))).argsort(axis=2)
            controls = [trajectories(model, x, donors, selectors, pred, order, boundary) for order in rankings]
            random = tuple(np.mean(np.stack(values), axis=0) for values in zip(*controls))
            for method in METHODS:
                a = scores[method][dim][1].reshape(len(conds), n, -1)[ci]
                tie = rankings[0]
                high = np.take_along_axis(tie, (-np.take_along_axis(a, tie, axis=1)).argsort(
                    axis=1, kind="stable"), axis=1)
                low = np.take_along_axis(tie, np.take_along_axis(a, tie, axis=1).argsort(
                    axis=1, kind="stable"), axis=1)
                trajectories_by_order = {
                    "high_first": trajectories(model, x, donors, selectors, pred, high, boundary),
                    "low_first": trajectories(model, x, donors, selectors, pred, low, boundary),
                    "random": random,
                }
                deletion, insertion = random
                trajectories_by_order["no_intervention"] = (
                    np.zeros_like(deletion), np.repeat(insertion[:, :1], insertion.shape[1], axis=1)
                )
                alignment = spearman(a, effect)
                for order, (deletion, insertion) in trajectories_by_order.items():
                    meta = dict(horizon_min=h, condition=conds[ci], method=method,
                                dimension=dim, ordering=order)
                    rows.append(pd.DataFrame(meta | dict(
                        sample_id=sample.sample_id, compartment=sample.group, week=sample.week,
                        deletion_auc=np.trapezoid(deletion, dx=1 / len(selectors), axis=1),
                        insertion_auc=np.trapezoid(insertion, dx=1 / len(selectors), axis=1),
                        comprehensiveness=deletion[:, 1], sufficiency=insertion[:, 1],
                        alignment=alignment if order == "high_first" else np.nan,
                        untestable_group_fraction=untestable,
                    )))
                    curves.extend(curve_rows(meta, sample, deletion, insertion))
    return rows, curves, diagnostic


def load_frozen(root):
    directory = root / "frozen"
    manifest = json.loads((directory / "model_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["artifact_sha256"].items():
        assert sha(directory / name) == expected, name
    assert sha(directory / "feature_order.csv") == manifest["feature_order_sha256"]
    assert sha(root / "mask_manifest.csv") == manifest["mask_manifest_sha256"]
    models = {}
    for row in manifest["models"]:
        path = directory / row["file"]
        assert sha(path) == row["sha256"], path
        models[row["horizon_min"]] = lgb.Booster(model_file=str(path))
    return directory, np.load(directory / "windows_and_masks.npz"), models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    args = parser.parse_args()
    out = args.output
    directory, data, models = load_frozen(out)
    sample = pd.read_csv(directory / "sample_manifest.csv", parse_dates=["time", "week"])
    train = pd.read_csv(directory / "training_background_manifest.csv", parse_dates=["time"])
    feature = pd.read_csv(directory / "feature_order.csv")
    val = data["validation_x"].reshape(-1, 52)
    val_masked = np.where(data["validation_masks"][1].reshape(-1, 52), np.nan, val)
    thresholds = {}
    for h, model in models.items():
        delta = abs(predict(model, val_masked) - predict(model, val))
        q25, q75 = np.quantile(delta, [.25, .75])
        thresholds[str(h)] = dict(validation_n=len(val), q25=float(q25), q75=float(q75),
                                  low=float(min(.25, q25)), high=float(max(1, q75)), large_l1=.50)
    json_write(directory / "validation_thresholds.json", thresholds)
    sample = sample.loc[sample.split.eq("test")].reset_index(drop=True)
    x = data["test_x"].reshape(-1, 52)
    background = data["background_x"].reshape(-1, 52)
    donor_records, donor_sets = [], {}
    for role in ("explainer", "evaluation"):
        ids, wc, clock = match_donors(x, sample.time, background, train, role)
        donor_sets[role] = background[ids]
        for i, sid in enumerate(sample.sample_id):
            for j in range(ids.shape[1]):
                donor_records.append(dict(sample_id=sid, role=role, donor_id=int(ids[i, j]),
                                          wc_abs_diff=wc[i, j], export_clock_hours_diff=clock[i, j]))
    csv(pd.DataFrame(donor_records), directory / "matched_donors.csv")
    masks, conditions, reps = data["test_masks"], data["condition"], data["repeat"]
    assert len(x) == len(sample) and len(conditions) == len(masks)
    masked = np.where(masks.reshape(len(conditions), len(x), 52), np.nan, x[None, :, :])
    assert np.isfinite(masked[:, :, ANCHOR]).all()
    flat = masked.reshape(-1, 52)
    expand = np.tile(donor_sets["explainer"], (len(conditions), 1, 1))
    attribute = {dim: [] for dim in DIMS}
    global_rows, stable_rows, faith, curves, diagnostic, additivity = [], [], [], [], [], []
    local = dict(sample_id=sample.sample_id.to_numpy(dtype=str), condition=conditions, repeat=reps)
    for h in HORIZONS:
        model = models[h]
        assert model.feature_name() == feature.name.to_list()
        predictions = predict(model, flat)
        phi, bias, error = shap_features(model, flat, predictions)
        additivity.append(dict(horizon_min=h, explained_rows=len(phi), max_additivity_error=error,
                               anchor_component_mean=x[:, ANCHOR].mean(), expected_delta=bias[0]))
        local[f"phi_{h}"] = phi.reshape(len(conditions), len(x), -1)
        local[f"bias_{h}"] = bias.reshape(len(conditions), len(x))
        local[f"pred_{h}"] = predictions.reshape(len(conditions), len(x))
        global_rows.extend(summarize_attributes(
            h, conditions, sample.group, phi, abs(phi), feature.name, "TreeSHAP"))
        print(h, "TreeSHAP complete", flush=True)
        grouped_scores = {method: {} for method in METHODS}
        for dim, (names, selectors) in EXPLAIN.items():
            grouped_scores["TreeSHAP"][dim] = grouped_shap(phi, selectors)
            grouped_scores["Occlusion"][dim] = occlusion(model, flat, expand, selectors, predictions)
            for method in METHODS:
                signed, absolute = grouped_scores[method][dim]
                local[f"{method}_{dim}_signed_{h}"] = signed.reshape(len(conditions), len(x), -1)
                local[f"{method}_{dim}_abs_{h}"] = absolute.reshape(len(conditions), len(x), -1)
                attribute[dim].extend(summarize_attributes(
                    h, conditions, sample.group, signed, absolute, names, method))
                stable_rows.append(window_stability(
                    h, conditions, reps, sample, masks, predictions, signed, absolute, method, dim))
            print(h, dim, "stability complete", flush=True)
        result, curve, check = faithfulness(
            h, model, masked, predictions.reshape(len(conditions), len(x)), sample,
            conditions, donor_sets["evaluation"], background, grouped_scores)
        faith.extend(result)
        curves.extend(curve)
        diagnostic.extend(check)
        print(h, "faithfulness complete", flush=True)
    for dim, filename in zip(DIMS, ("sensor", "time", "sensor_time")):
        csv(pd.DataFrame(attribute[dim]), out / f"attribution_by_{filename}.csv")
    csv(pd.DataFrame(global_rows).merge(
        feature, left_on="group_label", right_on="name", validate="many_to_one"
    ).drop(columns="name"), out / "attribution_global.csv")
    csv(pd.concat(stable_rows, ignore_index=True), out / "attribution_stability.csv")
    csv(pd.concat(faith, ignore_index=True), out / "faithfulness_window_metrics.csv")
    csv(pd.DataFrame(curves), out / "deletion_insertion_curves.csv")
    csv(pd.concat(diagnostic, ignore_index=True), out / "replacement_diagnostics.csv")
    csv(pd.DataFrame(additivity), out / "treeshap_additivity.csv")
    np.savez_compressed(out / "local_attributions.npz", **local)
    print("Attribution results saved", flush=True)


if __name__ == "__main__":
    main()
