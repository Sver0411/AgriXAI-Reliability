"""Generate nine figures from Stage 4 result tables."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from freeze_models import ROOT, SIGNALS

COLORS = ["#0c6078", "#b25239", "#78559b", "#586c7b"]
TIME = ["60-50 min", "45-35 min", "30-20 min", "15-0 min"]
SENSORS = ["Rockwool WC", "Rockwool temp.", "Air temp.", "Air RH"]
MODES = {
    "A_complete": "Complete", "B_record": "Record shape", "C_random": "Matched random",
    "D_local_20m": "Local device", "E_joint_20m": "Local + air",
    "F_old_20m": "Older local", "F_recent_20m": "Recent local",
}


def bar_ci(ax, at, rows, color, width=.33, label=None):
    vals = rows.estimate.to_numpy()
    err = np.vstack([vals - rows.ci_lo, rows.ci_hi - vals])
    err = np.maximum(0, err)
    ax.bar(at, vals, yerr=err, color=color, width=width, capsize=3, label=label)


def save(fig, path):
    fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT)
    root = parser.parse_args().output
    out = root / "figures"
    out.mkdir(exist_ok=True)
    read = lambda stem: pd.read_csv(root / f"{stem}.csv")
    sensor = read("attribution_by_sensor")
    cell = read("attribution_by_sensor_time")
    perturb = read("perturbation_comparison")
    rel = read("prediction_attribution_relationship")
    curves = read("deletion_insertion_curves")
    faith = read("faithfulness_metrics")
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
        "font.size": 10, "axes.titlesize": 13, "axes.titleweight": "bold",
        "axes.labelcolor": "#253a42", "text.color": "#20323a", "axes.edgecolor": "#809399",
    })
    fig, ax = plt.subplots(figsize=(7.6, 4.3), layout="constrained")
    for i, h in enumerate((60, 120)):
        rows = sensor.query("horizon_min==@h and scope=='pooled' and condition=='A_complete' and method=='TreeSHAP'")
        vals = rows.set_index("group_label").loc[SIGNALS, "share_of_absolute"]
        ax.barh(np.arange(4) + (i - .5) * .33, vals * 100, height=.31, color=COLORS[i], label=f"{h} min")
    ax.set(yticks=np.arange(4), yticklabels=SENSORS, xlabel="Share of absolute tree attribution (%)",
           title="01   Sensor importance in complete histories")
    ax.invert_yaxis()
    ax.legend(frameon=False)
    save(fig, out / "01_sensor_ranking.png")

    def heat(h, condition, column):
        sub = cell.query("horizon_min==@h and scope=='pooled' and condition==@condition and method=='TreeSHAP'")
        return sub.set_index("group_label").loc[[f"{s} | {t}" for s in SIGNALS for t in TIME], column].to_numpy().reshape(4, 4)

    def heat_figure(arrays, headings, cmap, symmetric, label, title, filename):
        vmax = max(np.max(abs(a)) for a in arrays)
        fig, axes = plt.subplots(1, len(arrays), figsize=(3.15 * len(arrays) + 1, 3.6),
                                 sharey=True, layout="constrained")
        for ax, array, heading in zip(axes, arrays, headings):
            im = ax.imshow(array, cmap=cmap, vmin=-vmax if symmetric else 0, vmax=vmax, aspect="auto")
            ax.set(xticks=range(4), xticklabels=[x.replace(" min", "") for x in TIME],
                   yticks=range(4), yticklabels=SENSORS, xlabel="Minutes before forecast", title=heading)
            for row, column in np.ndindex(array.shape):
                v = array[row, column]
                ax.text(column, row, f"{v:.2f}", ha="center", va="center", fontsize=8,
                        color="white" if abs(v) > vmax * .62 else "#172c33")
        fig.suptitle(title, fontweight="bold")
        fig.colorbar(im, ax=axes, shrink=.82, label=label)
        save(fig, out / filename)

    heat_figure([heat(h, "A_complete", "absolute_mean") for h in (60, 120)],
                ["60-min forecast", "120-min forecast"], "YlGnBu", False, "Mean absolute mass (pp)",
                "02   Sensor × history attribution under complete input", "02_temporal_heatmap.png")

    fig, ax = plt.subplots(figsize=(9, 4.4), layout="constrained")
    modes = list(MODES)[1:]
    for i, method in enumerate(("TreeSHAP", "Occlusion")):
        p = perturb.query("horizon_min==60 and scope=='pooled' and budget=='all' and dimension=='sensor_time' and metric=='normalized_l1' and method==@method")
        bar_ci(ax, np.arange(len(modes)) + (i - .5) * .34, p.set_index("condition").loc[modes],
               COLORS[i], label=method)
    ax.set(xticks=np.arange(len(modes)), xticklabels=[MODES[m] for m in modes],
           ylabel="Symmetric L1 distance", title="03   Attribution changes across missingness patterns")
    ax.legend(frameon=False)
    save(fig, out / "03_perturbation_comparison.png")

    modes = ["A_complete", "B_record", "C_random"]
    heat_figure([heat(60, mode, "signed_mean") for mode in modes], [MODES[m] for m in modes],
                "RdBu_r", True, "Mean signed contribution (pp)",
                "04   Direction of tree attributions under perturbation", "04_complete_perturbed.png")

    fig, axes = plt.subplots(1, 2, figsize=(9, 4.4), sharey=True, layout="constrained")
    threshold = json.loads((root / "frozen/validation_thresholds.json").read_text())["60"]
    for ax, mode, color in zip(axes, modes[1:], COLORS):
        t = rel.query("horizon_min==60 and dimension=='sensor_time' and method=='TreeSHAP' and condition==@mode")
        ax.scatter(t.abs_prediction_delta, t.normalized_l1, s=13, alpha=.38, color=color, linewidth=0)
        ax.axvline(threshold["low"], lw=.8, color="#777", ls="--")
        ax.axhline(.5, lw=.8, color="#777", ls="--")
        ax.set(title="Matched random (30-mask mean)" if mode == "C_random" else MODES[mode], xlabel="Absolute forecast change (pp)", ylim=(0, 1.03))
    axes[0].set_ylabel("Sensor × time attribution L1")
    fig.suptitle("05   Forecast movement and explanation movement", fontweight="bold")
    save(fig, out / "05_prediction_attribution_scatter.png")

    for number, experiment in ((6, "deletion"), (7, "insertion")):
        fig, axes = plt.subplots(1, 2, figsize=(9, 4.1), sharey=True, layout="constrained")
        for ax, method in zip(axes, ("TreeSHAP", "Occlusion")):
            v = curves.query("horizon_min==60 and condition=='A_complete' and dimension=='sensor_time' and scope=='pooled' and experiment==@experiment and method==@method")
            for i, order in enumerate(("high_first", "low_first", "random", "no_intervention")):
                row = v.loc[v.ordering.eq(order)].sort_values("fraction")
                ax.plot(row.fraction, row.estimate, color=COLORS[i], label=order.replace("_", " "),
                        ls="--" if order == "no_intervention" else "-")
                if order == "high_first":
                    ax.fill_between(row.fraction, row.ci_lo, row.ci_hi, color=COLORS[i], alpha=.14)
            ax.set(title=method, xlabel="Fraction of groups replaced" if number == 6 else "Fraction of groups restored")
        axes[0].set_ylabel("Absolute distance to original forecast (pp)")
        axes[1].legend(frameon=False, fontsize=8)
        fig.suptitle(f"0{number}   {experiment.capitalize()} with direct donor splicing", fontweight="bold")
        save(fig, out / f"0{number}_{experiment}.png")

    fig, axes = plt.subplots(1, 2, figsize=(8.5, 4), sharey=True, layout="constrained")
    for ax, h in zip(axes, (60, 120)):
        for i, mode in enumerate(modes[1:]):
            p = perturb.query("horizon_min==@h and condition==@mode and budget=='all' and method=='TreeSHAP' and dimension=='sensor_time' and metric=='normalized_l1'").set_index("scope")
            bar_ci(ax, np.arange(2) + (i - .5) * .34, p.loc[["Reference", "TheAutomators"]],
                   COLORS[i], label=MODES[mode])
        ax.set(xticks=range(2), xticklabels=["Reference", "TheAutomators"], title=f"{h}-min forecast")
    axes[0].set_ylabel("Symmetric L1 distance")
    axes[1].legend(frameon=False, fontsize=8)
    fig.suptitle("08   Attribution stability by held-out compartment", fontweight="bold")
    save(fig, out / "08_compartment_stability.png")

    fig, axes = plt.subplots(1, 2, figsize=(8.5, 4.1), layout="constrained")
    for ax, metric, title in zip(axes, ("deletion_auc", "insertion_auc"), ("Deletion AUC", "Insertion AUC")):
        for i, method in enumerate(("TreeSHAP", "Occlusion")):
            p = faith.query("horizon_min==60 and condition=='A_complete' and scope=='pooled' and ordering=='high_minus_random' and metric==@metric and method==@method").set_index("dimension")
            bar_ci(ax, np.arange(3) + (i - .5) * .34, p.loc[["sensor", "time", "sensor_time"]],
                   COLORS[i], label=method)
        ax.axhline(0, color="#60717b", lw=.8)
        ax.set(xticks=range(3), xticklabels=["Sensor", "Time", "Sensor × time"],
               ylabel="High-first minus random (pp)", title=title)
    axes[1].legend(frameon=False, fontsize=8)
    fig.suptitle("09   Faithfulness against random order (direct splice)", fontweight="bold")
    save(fig, out / "09_faithfulness_comparison.png")
    print("Nine figures saved", flush=True)


if __name__ == "__main__":
    main()
