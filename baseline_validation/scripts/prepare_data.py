
"""Verify the audited sources, then build windows within each preassigned group."""

from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

OUT = Path(__file__).resolve().parents[1]
STAGE2 = OUT.parent / "dataset_scouting_round2"
SIGNALS = ["moisture", "local_temp", "air_temp", "air_rh"]
HORIZONS = (30, 60, 120)
MINUTE = 60_000_000_000
PARMA_ROLES = {"2023": "train", "2024": "validation", "2025": "test"}
WUR_ROLES = {
    "AICU": "train", "Automatoes": "train", "Digilog": "train",
    "IUACAAS": "validation", "Reference": "test", "TheAutomators": "test",
}
DEPTH = {"2023": "20 cm README; 15 cm later paper", "2024": "15/30 cm unmapped",
         "2025": "15/30 cm unmapped"}


def verify_sources():
    archives = pd.read_csv(STAGE2 / "statistics/source_archive_checksums.csv")
    for row in archives.loc[archives.dataset.isin(["evolving", "wur"])].itertuples():
        path = STAGE2 / row.file
        if sha256(path.read_bytes()).hexdigest() != row.sha256:
            raise ValueError(f"Stage 2 archive checksum mismatch: {path}")
    members = pd.read_csv(STAGE2 / "statistics/raw_file_inventory.csv")
    for group in WUR_ROLES:
        for name in ("GreenhouseClimate.csv", "GrodanSens.csv"):
            member = f"{group}/{name}"
            expected = members.loc[
                (members.dataset == "wur") & (members.member == member), "sha256"
            ].item()
            path = STAGE2 / "raw/wur/extracted" / member
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Stage 2 member checksum mismatch: {path}")


def unique_clock(frame):
    """Remove exact duplicates and invalidate conflicting timestamps."""
    before = len(frame)
    frame = frame.drop_duplicates().sort_values("time").set_index("time")
    conflict = frame.index.duplicated(keep=False)
    conflicts = frame.index[conflict].nunique()
    frame.loc[conflict, :] = np.nan
    frame = frame.loc[~frame.index.duplicated()]
    return frame, before - len(frame), conflicts


def clock_ns(index):
    return index.as_unit("ns").asi8


def parma():
    with ZipFile(STAGE2 / "raw/evolving/original.zip") as archive:
        for group, role in PARMA_ROLES.items():
            def read(name, fields):
                path = next(x for x in archive.namelist() if x.endswith(f"/{group}/{name}{group}.csv"))
                with archive.open(path) as source:
                    frame = pd.read_csv(source, usecols=["ts_generation", *fields])
                frame["time"] = pd.to_datetime(frame.pop("ts_generation"), unit="ms", errors="raise")
                return frame

            air = read("environmental", ["generated_by", "temperature", "humidity"])
            soil = read("soil", ["line", "generated_by", "humidity", "temperature"])
            for table in (air, soil):
                if table.time.isna().any():
                    raise ValueError(f"Unparseable Parma timestamp in {group}")
            air = air.loc[air.time.dt.year == int(group)]
            if air.generated_by.nunique() != 1:
                raise ValueError(f"Unexpected number of weather stations in {group}")
            bounds = (air.time.min(), air.time.max())
            air, air_duplicates, air_conflicts = unique_clock(
                air.drop(columns="generated_by").rename(
                    columns={"temperature": "air_temp", "humidity": "air_rh"}
                )
            )
            inside_year = soil.time.dt.year == int(group)
            cross_year = int((~inside_year).sum())
            if group == "2024" and cross_year != 47895:
                raise ValueError(f"2024 cross-year rows differ from Stage 2: {cross_year}")
            same_year = soil.loc[inside_year]
            inside_support = same_year.time.between(*bounds)
            campaign_meta = dict(
                dataset="Parma", group=group, split=role, clock="UTC",
                cohort_raw_target_rows=len(soil), cohort_cross_year_rows=cross_year,
                cohort_outside_weather_support=int((~inside_support).sum()),
                cohort_start=str(bounds[0]), cohort_end=str(bounds[1]),
                weather_duplicate_rows=air_duplicates, weather_conflict_timestamps=air_conflicts,
                depth_cm_evidence=DEPTH[group],
            )
            soil = same_year.loc[inside_support]
            for (line, device), raw in soil.groupby(["line", "generated_by"], sort=True):
                local, duplicates, conflicts = unique_clock(
                    raw[["time", "humidity", "temperature"]].rename(
                        columns={"humidity": "moisture", "temperature": "local_temp"}
                    )
                )
                yield campaign_meta | dict(
                    unit=f"line_{line}", channel=str(device), native_minutes=10,
                    channel_raw_rows=len(raw), channel_duplicate_rows=duplicates,
                    channel_conflict_timestamps=conflicts,
                ), local, air


def wur_table(path, fields):
    frame = pd.read_csv(path, usecols=["%time", *fields], skipinitialspace=True, low_memory=False)
    time = pd.to_datetime(frame.pop("%time"), unit="D", origin="1899-12-30")
    rounded = time.dt.round("s")
    if time.isna().any() or ((rounded - time).abs() > pd.Timedelta(milliseconds=500)).any():
        raise ValueError(f"Unexpected WUR timestamp encoding: {path}")
    frame["time"] = rounded
    return frame


def wur():
    for group, role in WUR_ROLES.items():
        root = STAGE2 / "raw/wur/extracted" / group
        probes = wur_table(root / "GrodanSens.csv", ["WC_slab1", "WC_slab2", "t_slab1", "t_slab2"])
        air, air_duplicates, air_conflicts = unique_clock(
            wur_table(root / "GreenhouseClimate.csv", ["Tair", "Rhair"]).rename(
                columns={"Tair": "air_temp", "Rhair": "air_rh"}
            )
        )
        bounds = (max(probes.time.min(), air.index.min()), min(probes.time.max(), air.index.max()))
        meta = dict(
            dataset="WUR", group=group, unit=group, split=role, clock="Excel-export clock",
            native_minutes=5, cohort_raw_target_rows=len(probes), cohort_cross_year_rows=0,
            cohort_outside_weather_support=int((~probes.time.between(*bounds)).sum()),
            cohort_start=str(bounds[0]), cohort_end=str(bounds[1]),
            weather_duplicate_rows=air_duplicates, weather_conflict_timestamps=air_conflicts,
            depth_cm_evidence="rockwool slab; probe position not verified",
        )
        probes = probes.loc[probes.time.between(*bounds)]
        for slab in (1, 2):
            water, temp = f"WC_slab{slab}", f"t_slab{slab}"
            local, duplicates, conflicts = unique_clock(
                probes[["time", water, temp]].rename(columns={water: "moisture", temp: "local_temp"})
            )
            yield meta | dict(
                channel=water, channel_raw_rows=len(probes), channel_duplicate_rows=duplicates,
                channel_conflict_timestamps=conflicts,
            ), local, air.loc[bounds[0]:bounds[1]]


def recent_slots(source, grid, step, exact):
    observed = clock_ns(source.index)
    index = np.searchsorted(observed, grid, side="right") - 1
    lag = grid - observed[np.maximum(index, 0)]
    valid = (index >= 0) & (lag == 0 if exact else lag < step)
    values = np.full((len(grid), len(source.columns)), np.nan)
    values[valid] = source.to_numpy(dtype=float)[index[valid]]
    values[~np.isfinite(values)] = np.nan
    assert (lag[valid] >= 0).all()
    return values


def observed_labels(series, target, step, exact):
    values = series.dropna()
    times = clock_ns(values.index)
    if exact:
        return recent_slots(values.to_frame(), target, step, True)[:, 0], np.zeros(len(target))
    right = np.searchsorted(times, target)
    left = np.clip(right - 1, 0, len(times) - 1)
    right = np.clip(right, 0, len(times) - 1)
    chosen = np.where(target - times[left] <= times[right] - target, left, right)
    # At both ends the clipped index can lie on the same side of the requested time.
    offset = times[chosen] - target
    valid = (offset > -(step // 2)) & (offset <= step // 2)
    # At the exact lower boundary prefer the allowed upper observation, if present.
    fallback = (offset == -(step // 2)) & (times[right] - target == step // 2)
    chosen[fallback] = right[fallback]
    offset[fallback] = step // 2
    valid |= fallback
    labels = np.full(len(target), np.nan)
    labels[valid] = values.to_numpy()[chosen[valid]]
    return labels, offset / MINUTE


def build_channel(meta, local, air):
    local = local.copy()
    raw = local.moisture
    invalid_range = int((raw.notna() & ~raw.between(0, 100)).sum())
    local["moisture"] = raw.where(raw.between(0, 100))
    air = air.copy()
    air["air_rh"] = air.air_rh.where(air.air_rh.between(0, 100))
    finite = local.moisture.dropna()
    if finite.empty:
        raise ValueError(f"No usable observations for {meta}")
    minutes = meta["native_minutes"]
    step = minutes * MINUTE
    exact = meta["dataset"] == "WUR"
    freq = f"{minutes}min"
    # Bounds for each signal are recorded separately in the availability audit.
    first = max(finite.index.min(), pd.Timestamp(meta["cohort_start"])).ceil(freq)
    last = min(finite.index.max().ceil(freq), pd.Timestamp(meta["cohort_end"]).floor(freq))
    calendar = pd.date_range(first, last, freq=freq)
    grid = clock_ns(calendar)
    slots = np.column_stack((
        recent_slots(local, grid, step, exact), recent_slots(air, grid, step, exact)
    ))
    length = 60 // minutes + 1
    window = sliding_window_view(slots, length, axis=0).transpose(0, 2, 1)
    origins = grid[length - 1:]
    complete = np.isfinite(window).all(axis=(1, 2))
    mask = ~np.isfinite(window)
    lost = mask.sum(axis=(1, 2))
    train_masks = mask[(lost > 0) & (lost <= length * len(SIGNALS) / 2) & ~mask[:, -1, 0]]
    metrics = dict(
        **meta, grid_first=str(first), grid_last=str(last), grid_slots=len(grid),
        valid_raw_target_rows=len(finite), raw_target_out_of_range_rows=invalid_range,
        raw_target_zero_rows=int((finite == 0).sum()), complete_history_windows=int(complete.sum()),
        target_missing_slots=int((~np.isfinite(slots[:, 0])).sum()),
    )
    datasets = {}
    for horizon in HORIZONS:
        requested = origins + horizon * MINUTE
        label, offset = observed_labels(local.moisture, requested, step, exact)
        label_time = requested + np.rint(offset * MINUTE).astype(np.int64)
        allowed = complete & np.isfinite(label)
        allowed &= (label_time > origins) & (label_time <= pd.Timestamp(meta["cohort_end"]).value)
        allowed &= requested <= grid[-1]
        times = pd.to_datetime(origins[allowed], unit="ns")
        datasets[horizon] = (
            window[allowed],
            pd.DataFrame({
                "group": meta["group"], "unit": meta["unit"], "channel": meta["channel"],
                "split": meta["split"], "time": times,
                "week": times.to_period("W-SUN").start_time,
                "truth": label[allowed], "label_offset_min": offset[allowed],
            }),
        )
        metrics[f"windows_{horizon}min"] = int(allowed.sum())
        metrics[f"first_origin_{horizon}min"] = str(times.min())
        metrics[f"last_origin_{horizon}min"] = str(times.max())
    return datasets, metrics, train_masks, (calendar, slots, local, air)
