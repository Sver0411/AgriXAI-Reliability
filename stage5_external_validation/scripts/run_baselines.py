"""Run the preregistered, cross-season Horti-M3 prediction gate."""

import json

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from audit_horti import OUT, audit_channels, audit_tables, inventory, save

SEED = 20261108
HORIZONS = (30, 60, 120)
ROLES = {2023: "train", 2024: "validation", 2025: "test"}
FEATURES = ["wc", "soil_temp", "air_temp", "air_rh"]
LAGS = (0, 30, 60, 90)
CHANNELS = ("处理2", "处理5")
FAMILIES = {2023: "2023_export", 2024: "t2_t5_control2", 2025: "t2_t5_control2"}
MODELS = ("Persistence", "Ridge", "LightGBM")
BOOTSTRAPS = 2000


def measured_windows(slots, horizon):
    arrays, frames, attrition = [], [], []
    for year, role in ROLES.items():
        for channel in CHANNELS:
            rows = slots[(year, FAMILIES[year], channel)].set_index("slot")
            valid_wc = ~(rows[["wc", "soil_temp"]].eq(0).all(axis=1))
            valid_air = ~(rows[["air_temp", "air_rh", "co2"]].eq(0).all(axis=1))
            clean_input = rows[FEATURES].copy()
            clean_input.loc[~valid_wc, ["wc", "soil_temp"]] = np.nan
            clean_input.loc[~valid_air, ["air_temp", "air_rh"]] = np.nan
            origin = rows.time
            history, has_history = [], np.ones(len(rows), dtype=bool)
            for lag in LAGS:
                query = rows.index - pd.Timedelta(minutes=lag)
                values = clean_input.reindex(query).to_numpy()
                actual = rows.time.reindex(query).to_numpy()
                elapsed = (origin.to_numpy() - actual) / np.timedelta64(1, "m")
                slot_ok = np.isfinite(elapsed) & (np.abs(elapsed-lag) <= 5)
                if lag:
                    slot_ok &= elapsed > 0
                has_history &= slot_ok & np.isfinite(values).all(axis=1)
                history.append(values)
            future = rows.index + pd.Timedelta(minutes=horizon)
            label = rows.wc.where(valid_wc).reindex(future).to_numpy()
            label_time = rows.time.reindex(future).to_numpy()
            lead = (label_time - origin.to_numpy()) / np.timedelta64(1, "m")
            target_ok = np.isfinite(label) & (lead > 0) & (abs(lead-horizon) <= 5)
            allowed = has_history & target_ok
            x = np.column_stack(history)[allowed]
            x = np.column_stack((x, x[:, 4::4] - x[:, :1]))
            arrays.append(x)
            chosen = rows.loc[allowed]
            frames.append(pd.DataFrame(dict(
                year=year, split=role, channel=channel, origin=chosen.time.to_numpy(),
                label_time=label_time[allowed], lead_min=lead[allowed],
                week=chosen.time.dt.to_period("W-SUN").dt.start_time.to_numpy(),
                y=label[allowed], current=x[:, 0],
            )))
            attrition.append(dict(
                horizon_min=horizon, year=year, split=role, channel=channel, valid_slots=len(rows),
                same_row_valid_inputs=int(np.isfinite(clean_input).all(axis=1).sum()),
                complete_history=int(has_history.sum()), eligible_target=int(target_ok.sum()),
                final_windows=int(allowed.sum()),
                valid_weeks=int(frames[-1].week.nunique()),
                lead_min_observed=np.min(lead[allowed]) if allowed.any() else np.nan,
                lead_median=np.median(lead[allowed]) if allowed.any() else np.nan,
                lead_max_observed=np.max(lead[allowed]) if allowed.any() else np.nan,
            ))
    x = np.concatenate(arrays)
    frame = pd.concat(frames, ignore_index=True)
    assert np.isfinite(x).all()
    assert not frame.duplicated(["year", "channel", "origin"]).any()
    for year in ROLES:
        part = frame.loc[frame.year.eq(year)]
        assert part.origin.dt.year.eq(year).all() and part.label_time.dt.year.eq(year).all()
        assert (part.origin < part.label_time).all()
    return x, frame, attrition


def bootstrap(gain, weeks):
    codes, unique = pd.factorize(weeks, sort=True)
    count, total = np.bincount(codes), np.bincount(codes, weights=gain)
    if len(unique) < 4:
        return np.nan, np.nan
    draw = np.random.default_rng(SEED).integers(len(unique), size=(BOOTSTRAPS, len(unique)))
    dist = total[draw].sum(axis=1) / count[draw].sum(axis=1)
    return np.quantile(dist, [.025, .975])


def score(frame, pred):
    y = frame.y.to_numpy()
    baseline = abs(frame.current.to_numpy() - y)
    error = pred - y
    mae = abs(error).mean()
    base_mae = baseline.mean()
    lo, hi = bootstrap(baseline - abs(error), frame.week)
    return dict(
        n=len(frame), weeks=frame.week.nunique(), mae=mae, rmse=np.sqrt((error ** 2).mean()),
        persistence_mae=base_mae, mae_skill=1-mae/base_mae if base_mae else np.nan,
        mae_gain=base_mae-mae, gain_ci_lo=lo, gain_ci_hi=hi,
        baseline_changes_le_0_1pp=int((baseline <= .100001).sum()),
    )


def train_models(x, frame, horizon):
    train, validation = frame.split.eq("train"), frame.split.eq("validation")
    y = frame.y.to_numpy() - frame.current.to_numpy()
    candidates = {
        "Ridge": [(f"alpha={alpha:g}", make_pipeline(StandardScaler(), Ridge(alpha=alpha)))
                  for alpha in (.1, 1., 10.)],
        "LightGBM": [(f"num_leaves={leaves}", lgb.LGBMRegressor(
            num_leaves=leaves, n_estimators=220, learning_rate=.05, min_child_samples=100,
            random_state=SEED, n_jobs=2, deterministic=True, force_col_wise=True, verbosity=-1
        )) for leaves in (15, 31)],
    }
    fits, choices, results = {}, {}, []
    for name, models in candidates.items():
        best = float("inf")
        for params, model in models:
            model.fit(x.loc[train], y[train])
            val_loss = abs(model.predict(x.loc[validation]) - y[validation]).mean()
            results.append(dict(horizon_min=horizon, model=name, params=params, validation_mae=val_loss))
            if val_loss < best:
                best = val_loss
                fits[name] = model
                choices[name] = dict(params=params, validation_mae=float(val_loss))
    selected = min(choices, key=lambda name: choices[name]["validation_mae"])
    return fits, choices, selected, results


def main():
    sources, contents = inventory()
    save("source_manifest", sources, top_level=True)
    slots = audit_channels(audit_tables(sources, contents))
    experiments, attrition = {}, []
    for horizon in HORIZONS:
        x, frame, counts = measured_windows(slots, horizon)
        attrition.extend(counts)
        names = [f"{f}_{lag}m" for lag in LAGS for f in FEATURES]
        names.extend(f"wc_relative_{lag}m" for lag in LAGS[1:])
        experiments[horizon] = pd.DataFrame(x, columns=names), frame
    save("window_eligibility", attrition)
    enough = pd.DataFrame(attrition).query("horizon_min==60 and split=='test'")
    primary_frame = experiments[60][1]
    assert primary_frame.loc[primary_frame.split.eq("test")].week.nunique() >= 6
    assert enough.valid_weeks.ge(4).all()
    choices, runs, tuning = {}, {}, []
    for h, (x, frame) in experiments.items():
        fits, params, selected, tried = train_models(x, frame, h)
        choices[str(h)] = dict(selected_learning_model=selected, models=params)
        runs[h] = fits
        tuning.extend(tried)
    save("validation_search", tuning)
    config = dict(seed=SEED, primary_horizon_min=60, horizons_min=HORIZONS, history_lags_min=LAGS,
                  feature_order=names, channels=CHANNELS, season_roles=ROLES, choices=choices,
                  test_gate=dict(min_test_weeks=6, min_weeks_per_channel=4,
                                 require_positive_skill=True, require_positive_ci_lower=True,
                                 require_rmse_le_persistence=True, require_both_channels_improved=True))
    (OUT / "statistics/frozen_configuration.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    metrics, test_predictions = [], []
    for h, (x, frame) in experiments.items():
        current = frame.current.to_numpy()
        preds = {"Persistence": current}
        preds.update({name: current + model.predict(x) for name, model in runs[h].items()})
        for name, values in preds.items():
            for (year, split), group in frame.groupby(["year", "split"], sort=True):
                for ch, part in [("pooled", group), *group.groupby("channel", sort=True)]:
                    metrics.append(dict(
                        horizon_min=h, year=year, split=split, channel=ch, model=name,
                        selected_learning_model=name == choices[str(h)]["selected_learning_model"],
                    ) | score(part, values[part.index]))
        test = frame.loc[frame.split.eq("test")].copy()
        for name in MODELS:
            test[name] = preds[name][test.index]
        test_predictions.append(test.assign(horizon_min=h))
        print(h, frame.split.value_counts().to_dict(), choices[str(h)], flush=True)
    scores = pd.DataFrame(metrics)
    save("baseline_metrics", scores)
    save("heldout_predictions", pd.concat(test_predictions, ignore_index=True))
    test = scores.query("horizon_min==60 and split=='test'")
    pooled = test.loc[test.channel.eq("pooled")].set_index("model")
    selected = choices["60"]["selected_learning_model"]
    row = pooled.loc[selected]
    gate = bool(row.mae_skill > 0 and row.gain_ci_lo > 0 and
                row.rmse <= pooled.loc["Persistence", "rmse"] and
                test.loc[test.channel.ne("pooled") & test.model.eq(selected)].mae_skill.gt(0).all())
    print(f"Primary test gate: {'GO' if gate else 'NO-GO'}", flush=True)


if __name__ == "__main__":
    main()
