"""Reproduce both Stage 3 WUR boosters and freeze Stage 4 samples and masks."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

ROOT = Path(__file__).resolve().parents[1]
STAGE3 = ROOT.parent / "baseline_validation"
sys.path.insert(0, str(STAGE3 / "scripts"))
from missingness import SEED as STAGE3_SEED, paired_masks
from prepare_data import SIGNALS, build_channel, verify_sources, wur
from run_baselines import features, scores

SEED = 20261108
HORIZONS = (60, 120)
KEY = ["group", "channel", "time"]
ANCHOR = 48
N_RANDOM = 30
ATOL = 5e-8


def csv(frame, path):
    frame.to_csv(path, index=False, float_format="%.10g", lineterminator="\n")


def json_write(path, content):
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def pool():
    chunks = {h: [] for h in HORIZONS}
    bank, provenance = [], []
    for meta, local, air in wur():
        examples, _, masks, (calendar, slots, _, _) = build_channel(meta, local, air)
        for h in HORIZONS:
            chunks[h].append(examples[h])
        if meta["split"] == "train":
            missing = sliding_window_view(~np.isfinite(slots), 13, axis=0).transpose(0, 2, 1)
            count = missing.sum(axis=(1, 2))
            keep = (count > 0) & (count <= 26) & ~missing[:, -1, 0]
            np.testing.assert_array_equal(missing[keep], masks)
            bank.append(masks)
            provenance.extend(dict(group=meta["group"], channel=meta["channel"], time=t)
                              for t in calendar[12:][keep])
    windows = {h: (np.concatenate([x for x, _ in chunks[h]]),
                   pd.concat([f for _, f in chunks[h]], ignore_index=True)) for h in HORIZONS}
    return windows, np.concatenate(bank), pd.DataFrame(provenance)


def restore(h, x, frame, out, checks):
    summary = pd.read_csv(STAGE3 / "prediction_horizon_comparison.csv")
    expected = summary.query("dataset=='WUR' and horizon_min==@h and selected_by_validation").iloc[0]
    assert expected.model == "LightGBM"
    leaves = int(expected.parameters.split("=")[1])
    train, test = frame.split.eq("train"), frame.split.eq("test")
    z = features(x)
    delta = frame.truth.to_numpy() - x[:, -1, 0]
    fit = lgb.LGBMRegressor(num_leaves=leaves, n_estimators=220, learning_rate=.05,
                          min_child_samples=100, random_state=STAGE3_SEED, n_jobs=2,
                          deterministic=True, force_col_wise=True, verbosity=-1)
    fit.fit(z.loc[train], delta[train])
    model_path = out / f"lightgbm_{h}min.txt"
    fit.booster_.save_model(model_path)
    model = lgb.Booster(model_file=str(model_path))
    assert model.feature_name() == z.columns.tolist()
    np.testing.assert_array_equal(model.predict(z.loc[test], num_threads=2), fit.predict(z.loc[test]))
    persist = x[test, -1, 0]
    pred = persist + model.predict(z.loc[test], num_threads=2)
    test_frame = frame.loc[test].reset_index(drop=True)
    actual = scores(test_frame, pred, persist)
    for metric in ("n", "mae", "rmse", "r2", "mae_skill"):
        checks.append(dict(horizon_min=h, scope="pooled", metric=metric,
                           expected=float(expected[metric]), recovered=float(actual[metric])))
    by_group = pd.read_csv(STAGE3 / "per_group_metrics.csv")
    by_group = by_group.query("dataset=='WUR' and horizon_min==@h and level=='compartment' and split=='test' and model=='LightGBM'")
    for r in by_group.itertuples():
        g = test_frame.group.eq(r.group)
        mae = np.abs(test_frame.loc[g, "truth"] - pred[g]).mean()
        checks.append(dict(horizon_min=h, scope=r.group, metric="mae",
                           expected=r.mae, recovered=mae))
    return dict(file=model_path.name, sha256=sha(model_path), horizon_min=h,
                num_leaves=leaves, n_train=int(train.sum()), trees=model.num_trees())


def sample(frame, eligible, rng):
    selected = []
    for _, sub in frame.loc[eligible].groupby(["group", "channel", "week"], sort=True):
        selected.extend(rng.choice(sub.index, size=min(4, len(sub)), replace=False))
    return np.sort(selected)


def format_mask(mask):
    return np.packbits(mask.reshape(-1), bitorder="little").tobytes().hex()


def matched(mask, seed):
    rng = np.random.default_rng(seed)
    result = np.zeros_like(mask)
    for signal in range(4):
        n = 13 - int(signal == 0)
        rank = np.argsort(np.argsort(rng.random((len(mask), n)), axis=1), axis=1)
        result[:, :n, signal] = rank < mask[:, :, signal].sum(axis=1)[:, None]
    return result


def build_masks(structured, first_random, bank):
    conditions = ["A_complete", "B_record"] + ["C_random"] * N_RANDOM
    seeds = [None, STAGE3_SEED, *range(STAGE3_SEED, STAGE3_SEED + N_RANDOM)]
    reps = [0, 0, *range(N_RANDOM)]
    masks = [np.zeros_like(structured), structured, first_random]
    masks.extend(matched(structured, seed) for seed in seeds[3:])
    for candidate in masks[2:]:
        np.testing.assert_array_equal(candidate.sum(axis=1), structured.sum(axis=1))
    extensions = [("D_local_20m", 4, 2), ("E_joint_20m", 4, 4),
                  ("F_old_20m", 0, 2), ("F_recent_20m", 8, 2)]
    for name, start, channels in extensions:
        support = sliding_window_view(bank[:, :, :channels].all(axis=2), 4, axis=1).all(axis=2)
        if not support.any():
            raise ValueError(f"No corresponding contiguous training pattern for {name}")
        mask = np.zeros_like(structured)
        mask[:, start:start + 4, :channels] = True
        conditions.append(name)
        seeds.append(None)
        reps.append(0)
        masks.append(mask)
    masks = np.stack(masks)
    assert not masks[:, :, -1, 0].any()
    return conditions, reps, seeds, masks


def stress_mae(out, frame, inputs, structured, checks):
    booster = lgb.Booster(model_file=str(out / "lightgbm_60min.txt"))
    source = pd.read_csv(STAGE3 / "missingness_prediction_metrics.csv")
    expected = source.query("dataset=='WUR' and scope=='pooled' and model=='LightGBM'").iloc[0]
    for name, xx in zip(("complete", "record", "random"), inputs):
        pred = xx[:, -1, 0] + booster.predict(features(xx), num_threads=2)
        checks.append(dict(horizon_min=60, scope="stage3_stress", metric=f"mae_{name}",
                           expected=float(expected[f"mae_{name}"]),
                           recovered=float(np.abs(pred - frame.truth.to_numpy()).mean())))
    budgets = pd.read_csv(STAGE3 / "missingness_mask_totals.csv")
    budgets = budgets.query("dataset=='WUR' and test_scope=='pooled' and condition=='record_derived'").set_index("signal")
    for i, signal in enumerate(SIGNALS):
        checks.append(dict(horizon_min=60, scope="stage3_stress", metric=f"masked_{signal}",
                           expected=float(budgets.loc[signal, "masked"]),
                           recovered=float(structured[:, :, i].sum())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    args = parser.parse_args()
    out = args.output / "frozen"
    out.mkdir(parents=True, exist_ok=True)
    verify_sources()
    windows, bank, provenance = pool()
    checks = []
    models = [restore(h, *windows[h], out, checks) for h in HORIZONS]
    x60, f60 = windows[60]
    _, f120 = windows[120]
    future = f120.set_index(KEY)["truth"]
    rng = np.random.default_rng(SEED)
    sampled, inputs, manifests = [], {}, []
    for role in ("validation", "test"):
        keep = f60.split.eq(role)
        base, xs, (record, random) = paired_masks(
            x60[keep], f60.loc[keep].reset_index(drop=True), bank
        )
        pattern_idx = np.random.default_rng(STAGE3_SEED).integers(len(bank), size=len(base))
        np.testing.assert_array_equal(bank[pattern_idx], record)
        if role == "test":
            stress_mae(out, base, xs, record, checks)
        keys = pd.MultiIndex.from_frame(base[KEY])
        chosen = sample(base, keys.isin(future.index), rng)
        meta = base.iloc[chosen].reset_index(drop=True).rename(columns={"truth": "truth_60"})
        meta["truth_120"] = future.reindex(keys[chosen]).to_numpy()
        meta["sample_id"] = [f"{role[0]}{i:04d}" for i in range(len(meta))]
        sampled.append(meta)
        conditions, reps, seeds, masks = build_masks(record[chosen], random[chosen], bank)
        inputs[f"{role}_x"] = xs[0][chosen]
        inputs[f"{role}_masks"] = masks
        for condition, rep, seed, matrix in zip(conditions, reps, seeds, masks):
            for i, row in enumerate(meta.itertuples()):
                source = provenance.iloc[pattern_idx[chosen[i]]] if condition == "B_record" else None
                manifests.append(dict(
                    sample_id=row.sample_id, split=role, group=row.group, channel=row.channel,
                    origin=row.time, condition=condition, repeat=rep, seed=seed,
                    mask_little_endian_hex=format_mask(matrix[i]), n_masked=int(matrix[i].sum()),
                    **{f"masked_{signal}": int(matrix[i, :, j].sum()) for j, signal in enumerate(SIGNALS)},
                    training_mask_group=source.group if source is not None else "",
                    training_mask_channel=source.channel if source is not None else "",
                    training_mask_origin=source.time if source is not None else "",
                ))
    check = pd.DataFrame(checks)
    np.testing.assert_allclose(check.recovered, check.expected, atol=ATOL, rtol=0)
    check["abs_difference"] = abs(check.recovered - check.expected)
    csv(check, out / "stage3_reproduction.csv")
    print("Stage 3 checks passed", len(check), flush=True)
    train = np.flatnonzero(f60.split.eq("train"))
    selected = rng.choice(train, size=4096, replace=False)
    background = f60.iloc[selected][KEY].reset_index(drop=True)
    background["donor_id"] = np.arange(len(background))
    background["role"] = np.repeat(["explainer", "evaluation"], 2048)
    background["current_wc"] = x60[selected, -1, 0]
    csv(background, out / "training_background_manifest.csv")
    np.savez_compressed(out / "windows_and_masks.npz", **inputs, background_x=x60[selected],
                        condition=np.asarray(conditions), repeat=np.asarray(reps))
    meta = pd.concat(sampled, ignore_index=True)
    csv(meta, out / "sample_manifest.csv")
    csv(pd.DataFrame(manifests), args.output / "mask_manifest.csv")
    col = features(x60[:1]).columns.tolist()
    mapping = []
    for i, name in enumerate(col):
        k, j = divmod(i, 4) if i < 52 else (i - 52, 0)
        mapping.append(dict(index=i, name=name, lag_minutes=60 - k * 5,
                            signal=SIGNALS[j], derived=i >= 52, mutable=i != ANCHOR))
    csv(pd.DataFrame(mapping), out / "feature_order.csv")
    json_write(out / "model_manifest.json", dict(
        lightgbm=lgb.__version__, python=platform.python_version(), numpy=np.__version__,
        pandas=pd.__version__, models=models, feature_order_sha256=sha(out / "feature_order.csv"),
        preprocess=dict(slot_minutes=5, history_minutes=60, input_signals=SIGNALS,
                        current_wc_index=ANCHOR, residual_formula="forecast=current WC+Booster(delta)",
                        missing="native NaN; current WC protected",
                        timestamp="Excel serial converted to seconds per Stage 3",
                        train=["AICU", "Automatoes", "Digilog"], validation=["IUACAAS"],
                        test=["Reference", "TheAutomators"]),
        sample_seed=SEED, stage3_seed=STAGE3_SEED, random_repeats=N_RANDOM,
        stage3_sources={p: sha(STAGE3 / p) for p in (
            "scripts/prepare_data.py", "scripts/run_baselines.py", "scripts/missingness.py",
            "prediction_horizon_comparison.csv", "per_group_metrics.csv",
        )},
        artifact_sha256={p: sha(out / p) for p in (
            "sample_manifest.csv", "windows_and_masks.npz", "training_background_manifest.csv",
            "stage3_reproduction.csv",
        )},
        mask_manifest_sha256=sha(args.output / "mask_manifest.csv"),
    ))
    print(meta.groupby(["split", "group"]).size().to_string(), flush=True)


if __name__ == "__main__":
    main()
