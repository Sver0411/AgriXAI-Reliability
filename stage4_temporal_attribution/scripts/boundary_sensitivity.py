"""Check whether Stage 4 intervention conclusions depend on donor splice jumps."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from explain import METHODS, occlusion
from freeze_models import HORIZONS, ROOT, csv
from run_attribution import EXPLAIN, faithfulness, load_frozen, window_stability
from summarize import WINDOW_KEY, faithfulness_summaries, stability_summaries, STABILITY, PREDICTION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    out = parser.parse_args().output
    directory, frozen, models = load_frozen(out)
    sample = pd.read_csv(directory / "sample_manifest.csv", parse_dates=["time", "week"])
    sample = sample.loc[sample.split.eq("test")].reset_index(drop=True)
    donors = pd.read_csv(directory / "matched_donors.csv")
    bg = frozen["background_x"].reshape(-1, 52)
    groups = {role: bg[donors.loc[donors.role.eq(role), "donor_id"].to_numpy().reshape(len(sample), 2)]
              for role in ("explainer", "evaluation")}
    n = len(sample)
    masks, conditions, reps = frozen["test_masks"], frozen["condition"], frozen["repeat"]
    x = frozen["test_x"].reshape(n, 52)
    masked = np.where(masks.reshape(len(conditions), n, 52), np.nan, x[None, :, :])
    flat = masked.reshape(-1, 52)
    expand = np.tile(groups["explainer"], (len(conditions), 1, 1))
    attr = np.load(out / "local_attributions.npz")
    rows, diagnostics, stable, curves = [], [], [], []
    for h in HORIZONS:
        scores = {m: {} for m in METHODS}
        pred = attr[f"pred_{h}"].reshape(-1)
        for dim, (_, selectors) in EXPLAIN.items():
            scores["TreeSHAP"][dim] = (
                attr[f"TreeSHAP_{dim}_signed_{h}"].reshape(len(flat), -1),
                attr[f"TreeSHAP_{dim}_abs_{h}"].reshape(len(flat), -1),
            )
            corrected = occlusion(models[h], flat, expand, selectors, pred, boundary=True)
            scores["Occlusion"][dim] = corrected
            stable.append(window_stability(
                h, conditions, reps, sample, masks, pred, *corrected, "Occlusion", dim))
        fit, curve, check = faithfulness(
            h, models[h], masked, pred.reshape(len(conditions), n), sample, conditions,
            groups["evaluation"], bg, scores, boundary=True)
        rows.extend(fit)
        diagnostics.extend(check)
        curves.extend(curve)
        print(h, "boundary sensitivity complete", flush=True)
    raw = pd.concat(stable, ignore_index=True)
    window = raw.groupby(WINDOW_KEY, as_index=False)[[*STABILITY, *PREDICTION]].mean()
    csv(stability_summaries(window), out / "boundary_sensitivity_stability.csv")
    csv(faithfulness_summaries(pd.concat(rows, ignore_index=True)), out / "boundary_sensitivity_faithfulness.csv")
    csv(pd.concat(diagnostics, ignore_index=True), out / "boundary_sensitivity_diagnostics.csv")
    csv(pd.DataFrame(curves), out / "boundary_sensitivity_curves.csv")


if __name__ == "__main__":
    main()
