"""Static plots from observed values and the audit's intermediate results."""
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "figures"
BLUE, TEAL, ORANGE = "#254b75", "#16847f", "#bb5a2c"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.labelcolor": "#263548", "text.color": "#263548"})


def save(fig, name):
    fig.savefig(OUT / (name + ".png"), dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def timeline(ax, start, end, i, color, lw=4):
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    if b - a < pd.Timedelta(days=2):
        ax.plot(a + (b - a) / 2, i, "o", color=color, ms=4)
    else:
        ax.hlines(i, a, b, color=color, lw=lw)


def plot_audit(results, wp, joint):
    OUT.mkdir(exist_ok=True)
    quality = pd.DataFrame(results["timestamp_quality"])
    dated = quality[quality["first"] != ""]
    fig, ax = plt.subplots(figsize=(11.5, 10))
    labels = []
    for i, row in enumerate(dated.itertuples()):
        labels.append(f"{Path(row.file).stem} · {row.sheet}" if row.sheet != "(CSV)" else Path(row.file).stem + " · CSV")
        timeline(ax, row.first, row.last, i, BLUE if row.role == "Environment" else ORANGE)
    ax.set_yticks(range(len(labels)), labels, fontsize=8)
    ax.invert_yaxis()
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_title("Archive coverage by source | parseable time bounds", loc="left", weight="bold")
    ax.set_xlabel("Original clock dates • spans may contain gaps • blue environment / orange plant")
    ax.grid(axis="x", alpha=.22)
    save(fig, "01_overall_coverage")

    inventory = pd.DataFrame(results["file_inventory"]).dropna(subset=["campaign_year"])
    totals = inventory.groupby(["site", "campaign_year"]).n_data_rows.sum()
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, (location, values) in enumerate(totals.groupby(level=0)):
        vals = values.droplevel(0)
        ax.bar(vals.index, vals.values, label=location, color=[TEAL, BLUE][i], width=.65)
        ax.bar_label(ax.containers[-1], fmt="%d", fontsize=8, padding=2)
    ax.set_ylim(0, 1.18 * totals.max())
    ax.set_ylabel("Source observation rows")
    ax.set_title("Volume by site and campaign | raw rows, copies included", loc="left", weight="bold")
    ax.legend(frameon=False)
    save(fig, "02_site_year_distribution")

    frequency = pd.DataFrame(results["sampling_frequency"])
    series = [
        ("Central_2023.xlsx", "T", "2023 central T"),
        ("S_2022.xlsx", "T", "2022 top T"),
        ("Light_2022.xlsx", "G", "2022 irradiance"),
        ("S3_2020.csv", "base_Temperature", "2020 S3 base T"),
        ("Light_2019.xlsx", "G", "2019 irradiance"),
        ("Transpiration_2022.xlsx", "Scale1", "2022 scale 1"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6), layout="constrained")
    for ax, (file, field, title) in zip(axes.flat, series, strict=True):
        r = frequency[frequency.file.str.endswith(file) & frequency.field.eq(field) & frequency.calendar_year.eq("all")].iloc[0]
        bins = json.loads(r.n_bins)
        x = np.array([float(k) for k in bins])
        y = list(bins.values())
        ax.scatter(x, y, s=11, color=TEAL, alpha=.75)
        ax.set_xscale("symlog", linthresh=1)
        ax.set_yscale("log")
        ax.set_title(title, loc="left")
        ax.set_xlabel("Seconds (rounded)")
        ax.set_ylabel("Interval count")
        mode = x[np.argmax(y)]
        ax.text(.98, .94, f"Most common bin: {mode:g} s", transform=ax.transAxes,
                ha="right", va="top", fontsize=8, color=BLUE)
        if len(x) == 1:
            ax.set_xticks([mode], [f"{mode:g}"])
    fig.suptitle("Sampling intervals | unique usable timestamps per channel", weight="bold")
    save(fig, "03_sampling_intervals")

    fields = pd.DataFrame(results["column_dictionary"])
    data = fields[fields.role != "metadata"].copy()
    fig, axes = plt.subplots(1, 2, figsize=(12, 7), sharex=True, layout="constrained")
    for ax, role in zip(axes, ("Environment", "Plant"), strict=True):
        f = data[data.role == role].sort_values(["campaign_year", "file", "sheet", "column"])
        ix = np.arange(len(f))
        ax.barh(ix, 100 * f.missing_fraction, height=1, color=TEAL if role == "Environment" else ORANGE)
        changes = f.campaign_year.ne(f.campaign_year.shift()).to_numpy().nonzero()[0]
        ax.set_yticks(changes, f.campaign_year.iloc[changes].astype(str))
        for at in changes[1:]:
            ax.axhline(at - .5, color="#c5ccd1", lw=.7)
        ax.invert_yaxis()
        ax.set_title(f"{role} · {len(f)} source columns", loc="left")
        ax.set_xlabel("Missing cells (%)")
        ax.set_xlim(0, 102)
    fig.suptitle("Missingness by field instance | campaigns shown on the vertical axis", weight="bold")
    save(fig, "04_field_missingness")

    fig, axes = plt.subplots(1, 4, figsize=(23, 22), sharex=True, layout="constrained")
    data = data.sort_values(["role", "campaign_year", "file", "sheet", "column"])
    for i, ax in enumerate(axes):
        subset = data.iloc[i*93:(i+1)*93]
        labels = [f"{Path(r.file).stem}/{r.sheet}: {r.original_field}" for r in subset.itertuples()]
        y = np.arange(len(labels))
        rate = 100 * subset.missing_fraction
        ax.barh(y, rate, height=.65, color=[TEAL if r == "Environment" else ORANGE for r in subset.role])
        ax.scatter(np.zeros(sum(rate == 0)), y[rate == 0], color="#7a838a", s=3, clip_on=False)
        ax.set_yticks(y, labels, fontsize=6)
        ax.set_ylim(92.5, -.5)
        ax.set_xlim(0, 100)
        ax.set_xlabel("Missing cells (%)")
        ax.grid(axis="x", alpha=.22)
    fig.suptitle("Missing rates by source, sheet and original field | dot marks zero", weight="bold")
    fig.savefig(OUT / "08_field_missingness_detail.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    target = pd.DataFrame(results["target_label_audit"])
    pairing = pd.DataFrame(results["variable_alignment"])
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), layout="constrained")
    label_times = target[target.scope == "sampling_time"].copy()
    label_times["date"] = pd.to_datetime(label_times["first"]).dt.date
    for ax, (day, yy) in zip(axes, label_times.groupby("date"), strict=True):
        year = day.year
        al = pairing[(pairing.kind == "target_to_environment") & (pairing.campaign_year == year)]
        # The exact event-level markers come from the audited joint-window masks.
        days = pd.to_datetime(yy["first"])
        for i, lead in enumerate((0, 30, 60)):
            matched = yy[f"joint_60m_lead_{lead}"].eq(True).to_numpy()
            ax.scatter(days[~matched], np.full(sum(~matched), i), color=ORANGE, marker="x", s=30)
            ax.scatter(days[matched], np.full(sum(matched), i), color=TEAL, s=25)
        ax.set_yticks(range(3), ["0 min lead", "30 min lead", "60 min lead"])
        ax.set_title(f"{day} | {len(al)} required indoor channels, 60-minute input window", loc="left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.grid(axis="x", alpha=.2)
    fig.suptitle("Joint T/RH/light availability at label events | green usable, orange fails", weight="bold")
    save(fig, "07_label_input_windows")

    fig, ax = plt.subplots(figsize=(11, 4.5))
    for i, (year, (grid, accepted)) in enumerate(joint.items()):
        ax.scatter(grid, np.full(len(grid), i), color="#ccd2d9", marker="|", s=40)
        ax.scatter(accepted, np.full(len(accepted), i), color=TEAL, marker="|", s=40)
    ax.set_yticks(range(len(joint)), [str(y) for y in joint])
    ax.invert_yaxis()
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlabel("Original clock date • gray tested / teal all three channels passed")
    ax.set_title("Joint T/RH/light coverage | hourly checks of 60-minute raw windows", loc="left", weight="bold")
    ax.grid(axis="x", alpha=.18)
    save(fig, "05_joint_variable_windows")

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    canonical = [wp["WP_2022.xlsx"], wp["WP_2023.xlsx"]]
    cols = [f"WP{i}" for i in range(1, 5)]
    day_counts = []
    for i, df in enumerate(canonical):
        color = [TEAL, ORANGE][i]
        values = df[cols].stack().to_numpy()
        a.hist(values, bins=np.linspace(-.80, -.075, 16), histtype="step", lw=2, color=color, label=str(2022+i))
        for day, group in df.groupby(df.Time.dt.date):
            day_counts.append((str(day), group.Time.nunique(), color))
    a.set(xlabel="Measured stem water potential (MPa)", ylabel="Compartment values")
    a.legend(frameon=False)
    b.bar([x[0] for x in day_counts], [x[1] for x in day_counts], color=[x[2] for x in day_counts], width=.55)
    b.bar_label(b.containers[0])
    b.set(ylabel="Distinct recorded times", xlabel="Sampling day", ylim=(0, 11))
    fig.suptitle("Pressure-chamber labels | canonical 2022 and 2023 records", weight="bold")
    save(fig, "06_water_potential_labels")
