"""Report paired stability, prediction relationships, and faithfulness by compartment."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from freeze_models import ROOT, SEED, csv
from run_attribution import EVAL_METRICS
from stage4_stats import N_BOOT, block_mean, block_rank

STABILITY = ("spearman", "kendall_tau_b", "topk_overlap", "normalized_l1",
             "normalized_l2", "cosine", "sign_flip_rate")
PREDICTION = ("abs_prediction_delta", "abs_error_delta")
WINDOW_KEY = ["horizon_min", "method", "dimension", "condition", "sample_id",
              "compartment", "slab", "week", "masked"]


def scopes(frame):
    yield "pooled", frame
    yield from frame.groupby("compartment", sort=True)


def budget(frame):
    fraction = frame.masked / 52
    return np.select([fraction <= .10, fraction <= .25], ["<=10%", "10-25%"], default=">25%")


def stability_summaries(window):
    rows = []
    meta_cols = ["horizon_min", "method", "dimension", "condition"]
    window = window.assign(budget=budget(window))
    for meta, group in window.groupby(meta_cols, sort=True):
        for scope, sub in scopes(group):
            for band, part in [("all", sub), *sub.groupby("budget", sort=True)]:
                for metric in (*STABILITY, *PREDICTION):
                    rows.append(dict(zip(meta_cols, meta)) | dict(scope=scope, budget=band, metric=metric)
                                | block_mean(part[metric], part.week))
    contrasts = [("B_record", "C_random"), ("F_recent_20m", "F_old_20m"),
                 ("E_joint_20m", "D_local_20m")]
    labels = ["horizon_min", "method", "dimension"]
    for left, right in contrasts:
        cols = labels + ["sample_id", "compartment", "week"]
        joined = window.loc[window.condition.eq(left)].merge(
            window.loc[window.condition.eq(right)], on=cols, suffixes=("_l", "_r"),
            validate="one_to_one")
        if left == "B_record":
            np.testing.assert_array_equal(joined.masked_l, joined.masked_r)
        for meta, group in joined.groupby(labels, sort=True):
            for scope, sub in scopes(group):
                partitions = [("all", sub)]
                if left == "B_record":
                    partitions.extend(sub.groupby("budget_l", sort=True))
                for band, part in partitions:
                    for metric in (*STABILITY, *PREDICTION):
                        rows.append(dict(zip(labels, meta)) | dict(
                            condition=f"{left}_minus_{right}", scope=scope, budget=band, metric=metric
                        ) | block_mean(part[f"{metric}_l"] - part[f"{metric}_r"], part.week))
    return pd.DataFrame(rows)


def conditional_rate(event, eligible, week):
    numerator, denominator = np.asarray(event, dtype=float), np.asarray(eligible, dtype=float)
    codes, labels = pd.factorize(week, sort=True)
    s = np.bincount(codes, weights=numerator)
    c = np.bincount(codes, weights=denominator)
    n, n_week = int((denominator > 0).sum()), int((c > 0).sum())
    result = dict(estimate=s.sum() / c.sum() if n else np.nan, ci_lo=np.nan, ci_hi=np.nan,
                  n=n, weeks=n_week, eligible_equivalent=c.sum(), joint_equivalent=s.sum())
    if n >= 12 and n_week >= 4:
        draw = np.random.default_rng(SEED).integers(len(labels), size=(N_BOOT, len(labels)))
        a, b = s[draw].sum(axis=1), c[draw].sum(axis=1)
        valid = b > 0
        result["ci_lo"], result["ci_hi"] = np.quantile(a[valid] / b[valid], [.025, .975])
    return result


def relationship(raw, thresholds):
    h = raw.horizon_min.astype(str)
    raw = raw.assign(
        pred_small=raw.abs_prediction_delta <= h.map({k: v["low"] for k, v in thresholds.items()}),
        pred_large=raw.abs_prediction_delta >= h.map({k: v["high"] for k, v in thresholds.items()}),
        attr_large=raw.normalized_l1 >= .5,
    )
    raw["small_and_large_attr"] = raw.pred_small & raw.attr_large
    raw["large_and_large_attr"] = raw.pred_large & raw.attr_large
    raw["small_and_rank_changed"] = raw.pred_small & (raw.topk_overlap < .999999)
    cols = [*STABILITY, *PREDICTION, "prediction_delta", "pred_complete", "pred_perturbed", "truth",
            "pred_small", "pred_large", "attr_large", "small_and_large_attr", "large_and_large_attr",
            "small_and_rank_changed"]
    w = raw.groupby(WINDOW_KEY, as_index=False, sort=True)[cols].mean()
    w["rank_distance"] = 1 - w.spearman
    rows = []
    m = ["horizon_min", "method", "dimension", "condition"]
    for k, d in w.groupby(m, sort=True):
        for scope, sub in scopes(d):
            meta = dict(zip(m, k)) | dict(scope=scope)
            for metric, y in (("rho_delta_vs_l1", sub.normalized_l1),
                              ("rho_delta_vs_rank_distance", sub.rank_distance),
                              ("rho_error_change_vs_l1", sub.normalized_l1)):
                x = sub.abs_error_delta if metric == "rho_error_change_vs_l1" else sub.abs_prediction_delta
                rows.append(meta | dict(metric=metric) | block_rank(x, y, sub.week))
            if k[2] == "sensor_time":
                for metric, ev, sup in (
                    ("Pr_large_L1_given_small_delta", "small_and_large_attr", "pred_small"),
                    ("Pr_large_L1_given_large_delta", "large_and_large_attr", "pred_large"),
                    ("Pr_changed_TopK_given_small_delta", "small_and_rank_changed", "pred_small"),
                ):
                    rows.append(meta | dict(metric=metric) | conditional_rate(sub[ev], sub[sup], sub.week))
    return w, pd.DataFrame(rows)


def faithfulness_summaries(w):
    meta_cols = ["horizon_min", "condition", "method", "dimension", "ordering"]
    rows = []
    for k, group in w.groupby(meta_cols, sort=True):
        for scope, sub in scopes(group):
            metrics = [*EVAL_METRICS, "alignment"] if k[-1] == "high_first" else EVAL_METRICS
            for metric in metrics:
                rows.append(dict(zip(meta_cols, k)) | dict(scope=scope, metric=metric)
                            | block_mean(sub[metric], sub.week))
    ids = ["sample_id", "compartment", "week"]
    pair_meta = meta_cols[:-1]
    for k, group in w.groupby(pair_meta, sort=True):
        high = group.loc[group.ordering.eq("high_first")]
        for control in ("random", "low_first"):
            comp = high.merge(group.loc[group.ordering.eq(control)], on=ids, suffixes=("_l", "_r"),
                              validate="one_to_one")
            for scope, sub in scopes(comp):
                for metric in EVAL_METRICS:
                    rows.append(dict(zip(pair_meta, k)) | dict(ordering=f"high_minus_{control}",
                                scope=scope, metric=metric)
                                | block_mean(sub[f"{metric}_l"] - sub[f"{metric}_r"], sub.week))
    for k, group in w.loc[w.ordering.eq("high_first")].groupby(
            ["horizon_min", "condition", "dimension"], sort=True):
        comp = group.loc[group.method.eq("Occlusion")].merge(
            group.loc[group.method.eq("TreeSHAP")], on=ids, suffixes=("_l", "_r"),
            validate="one_to_one")
        for scope, sub in scopes(comp):
            for metric in (*EVAL_METRICS, "alignment"):
                rows.append(dict(horizon_min=k[0], condition=k[1], dimension=k[2],
                                method="Occlusion_minus_TreeSHAP", ordering="high_first",
                                scope=scope, metric=metric)
                            | block_mean(sub[f"{metric}_l"] - sub[f"{metric}_r"], sub.week))
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    out = parser.parse_args().output
    read = lambda name: pd.read_csv(out / f"{name}.csv", parse_dates=["week"])
    thresholds = json.loads((out / "frozen/validation_thresholds.json").read_text(encoding="utf-8"))
    detailed, rel = relationship(read("attribution_stability"), thresholds)
    comparison = stability_summaries(detailed)
    faith = faithfulness_summaries(read("faithfulness_window_metrics"))
    for name, frame in (
        ("prediction_attribution_relationship", detailed),
        ("prediction_relationship_summary", rel),
        ("perturbation_comparison", comparison),
        ("faithfulness_metrics", faith),
        ("per_compartment_results", pd.concat([
            frame.loc[frame.scope.ne("pooled")].assign(section=name)
            for name, frame in (("stability", comparison), ("relationship", rel), ("faithfulness", faith))
        ], ignore_index=True)),
    ):
        csv(frame, out / f"{name}.csv")
    print("Summaries saved", flush=True)


if __name__ == "__main__":
    main()
