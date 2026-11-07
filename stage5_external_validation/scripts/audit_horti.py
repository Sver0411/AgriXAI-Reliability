"""Audit the selected Horti-M3 members and retain source provenance."""

from hashlib import sha256
from itertools import combinations
from pathlib import Path, PurePosixPath
import re
import sys
from zlib import crc32

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1]
ROUND2 = OUT.parent / "dataset_scouting_round2"
CATALOG = ROUND2 / "raw/catalog"
RAW = ROUND2 / "raw/horti/zip_members"
sys.path.insert(0, str(ROUND2 / "scripts"))
from audit import tables

FIELDS = {
    "土壤水分": ("wc", "substrate volumetric water content", "% v/v"),
    "土壤温度": ("soil_temp", "substrate temperature", "°C"),
    "空气温度": ("air_temp", "air temperature", "°C"),
    "相对湿度": ("air_rh", "air relative humidity", "% RH"),
    "光照": ("light", "illuminance", "lx in paper; scale unresolved unless header says klux"),
    "二氧化碳": ("co2", "carbon dioxide concentration", "ppm"),
    "电压": ("voltage", "logger voltage", "V"),
    "时间": ("time", "export clock", "calendar time; UTC offset unverified"),
}
MANUAL = {
    "株高": ("plant height", "cm"), "高度": ("plant height", "cm"),
    "茎粗": ("stem diameter", "mm"),
    "叶宽": ("leaf width", "cm"), "叶长": ("leaf length", "cm"),
    "叶面积": ("leaf area", "cm²"), "叶片数": ("leaf count", "count"),
    "NDVI": ("normalized difference vegetation index", "dimensionless"),
    "RVI": ("ratio vegetation index", "dimensionless"),
    "LAI": ("leaf area index", "dimensionless"),
    "LNC": ("leaf nitrogen content", "see paper Table 10"),
    "LNA": ("leaf nitrogen accumulation", "see paper Table 10"),
    "LDW": ("leaf dry weight estimate", "see paper Table 10"),
}
WINDOWS = {
    2023: ("2023-05-01", "2023-07-02"),
    2024: ("2024-09-21", "2024-11-14"),
    2025: ("2025-04-19", "2025-06-14"),
}
GROUP = r"[（(]((?:处理|对照)[0-9]+)[）)]"
FILE_GROUP = r"处理[0-9]+"
MINUTE = pd.Timedelta(minutes=1)


def save(name, rows, top_level=False):
    path = OUT / f"{name}.csv" if top_level else OUT / "statistics" / f"{name}.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig",
                              lineterminator="\n", float_format="%.9g")


def inventory():
    archive = pd.read_csv(CATALOG / "horti_zip_members.csv", dtype={"crc32": str})
    picks = (CATALOG / "horti_selection.txt").read_text(encoding="utf-8-sig").splitlines()
    if len(picks) != len(set(picks)) or archive.member.duplicated().any():
        raise ValueError("Duplicate member name in source catalog or selection")
    meta = archive.set_index("member")
    rows, contents, seen = [], {}, {}
    for name in picks:
        item = meta.loc[name]
        parts = PurePosixPath(name).parts
        path = RAW.joinpath(*(part.replace("%", "%25").replace(":", "%3A") for part in parts))
        season = int(parts[1]) if re.fullmatch(r"20\d\d", parts[1]) else None
        kind = "sensor" if "/sensor_data/" in name else (
            "phenotype" if re.search(r"/grow(?:th)?_index/", name) else "supplement")
        payload = path.read_bytes()
        digest = sha256(payload).hexdigest()
        matched = len(payload) == item.bytes and f"{crc32(payload):08x}" == item.crc32.lower().zfill(8)
        if not matched:
            raise ValueError(f"ZIP catalog size/CRC mismatch: {name}")
        # Same season, source type and basename guard against merging distinct channel identities.
        key = (season, kind, path.name, digest)
        canonical = seen.setdefault(key, name)
        rows.append(dict(
            member=name, local_path=path.relative_to(OUT.parent).as_posix(), year=season, kind=kind,
            bytes=len(payload), zip_crc32=item.crc32.lower().zfill(8), sha256=digest,
            canonical_member=canonical, exact_file_duplicate=(canonical != name), status="verified",
        ))
        contents[name] = payload
    counts = (archive.loc[~archive.member.isin(picks), "member"].map(archive_category)
              .value_counts().sort_index().rename_axis("category").reset_index(name="unacquired_members"))
    save("unacquired_archive_summary", counts)
    return rows, contents


def archive_category(name):
    lower = name.lower()
    if "/images/" in lower:
        return "images"
    if re.search(r"/(?:processed|processed[_ ]data|process_data)/", lower):
        return "processed"
    if "/raw_data/" in lower:
        return "other_raw"
    return "other_supplement_or_layout"


def clock(frame, kind, name):
    if "时间" in frame:
        raw = frame["时间"]
        parsed = pd.to_datetime(raw, format="mixed", errors="coerce")
        fmt = "Excel datetime" if pd.api.types.is_datetime64_any_dtype(raw) else "YYYY/M/D H:MM"
        return parsed, "时间", fmt
    if kind == "phenotype":
        if "日期" in frame:
            return pd.to_datetime(frame["日期"], format="mixed", errors="coerce"), "日期", "calendar date, no clock"
        match = re.search(r"/(20\d{6})\.csv$", name)
        if match:
            day = pd.to_datetime(match[1], format="%Y%m%d")
            return pd.Series(day, index=frame.index), "(filename date)", "calendar date, no clock"
    return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]"), "", ""


def cadence(time):
    valid = time.dropna()
    sorted_unique = pd.Series(valid.unique()).sort_values()
    interval = sorted_unique.diff().dropna().dt.total_seconds()
    phase = (sorted_unique - sorted_unique.dt.floor("30min")).dt.total_seconds()
    return dict(
        timestamped_rows=len(valid), unique_times=len(sorted_unique),
        duplicate_time_excess=len(valid) - len(sorted_unique),
        backward_steps_in_source=int((valid.diff() < pd.Timedelta(0)).sum()),
        first=sorted_unique.min(), last=sorted_unique.max(),
        days=int(sorted_unique.dt.normalize().nunique()),
        intervals=len(interval), median_interval_s=interval.median(),
        near_30min_intervals=int(interval.between(25 * 60, 35 * 60).sum()),
        max_interval_s=interval.max(), gaps_gt_45min=int((interval > 45 * 60).sum()),
        gaps_gt_24h=int((interval > 86400).sum()),
        phase_median_s=phase.median(), phase_p95_s=phase.quantile(.95),
        phase_outside_0_10min=int((phase > 600).sum()),
    )


def column_info(field, kind, fallback):
    if kind == "sensor":
        base = next((key for key in FIELDS if field.startswith(key)), None)
        if base is None:
            return "", field, "", ""
        code, meaning, unit = FIELDS[base]
        if code == "light" and field.lower().endswith("klux"):
            unit = "klux (header)"
        group = re.search(GROUP, field)
        return code, meaning, unit, group[1] if group else fallback if code not in {"time", "voltage"} else ""
    if kind == "phenotype" and field in FIELDS and field != "时间":
        return "", "date-level attached environmental field; provenance not established", "", ""
    meaning, unit = MANUAL.get(field, (field, ""))
    return "", meaning, unit, ""


def logical_family(year, wc_groups):
    if year == 2023:
        return "2023_export"
    key = tuple(sorted(wc_groups))
    return {
        ("处理1", "对照2"): "front_t1_control2",
        ("处理2", "处理5", "对照2"): "t2_t5_control2",
        ("处理1", "处理6"): "back_t1_middle_t6",
    }.get(key, "unknown_schema")


def count_conflict_times(frame):
    if frame.empty:
        return 0
    distinct = frame.drop_duplicates()
    return int(distinct.loc[distinct.time.duplicated(keep=False), "time"].nunique())


def audit_tables(sources, contents):
    sheets, fields, mapping, records, file_rows = [], [], [], [], []
    for source in sources:
        name, kind, year = (source[k] for k in ("member", "kind", "year"))
        name_treatment = re.search(FILE_GROUP, PurePosixPath(name).stem)
        fallback = name_treatment[0] if name_treatment else ""
        order_times = []
        for sheet, frame, fmt in tables(contents[name], name):
            ts, time_key, time_fmt = clock(frame, kind, name)
            order_times.append(ts.dropna().reset_index(drop=True))
            identity = dict(member=name, year=year, kind=kind, sheet=sheet,
                            file_duplicate=source["exact_file_duplicate"])
            measured = ts.notna()
            snap = frame.loc[measured].copy()
            snap["time"] = ts[measured]
            timed = cadence(ts)
            if kind != "sensor":
                # A file-day is shared by design and is not a sensor clock or a duplicate key.
                for label in ("duplicate_time_excess", "backward_steps_in_source",
                              "near_30min_intervals", "phase_median_s",
                              "phase_p95_s", "phase_outside_0_10min",
                              "gaps_gt_45min", "gaps_gt_24h"):
                    timed[label] = None
            sheet_day = pd.to_datetime(sheet, format="%Y-%m-%d", errors="coerce")
            sheets.append(identity | dict(
                rows=len(frame), columns=len(frame.columns), format=fmt, time_key=time_key,
                timestamp_format=time_fmt, timezone="unverified" if time_key else "",
                unparsed_or_missing_timestamp=int((~measured).sum()) if kind == "sensor" else None,
                exact_duplicate_rows=int(frame.duplicated().sum()),
                conflicting_timestamps=count_conflict_times(snap) if kind == "sensor" else None,
                outside_sheet_day=int((ts[measured].dt.normalize() != sheet_day).sum())
                if pd.notna(sheet_day) else None,
            ) | timed)
            by_signal = {}
            for field in frame:
                code, meaning, unit, label = column_info(field, kind, fallback)
                values = frame[field]
                num = pd.to_numeric(values, errors="coerce") if code != "time" else None
                finite = num.where(np.isfinite(num)).dropna() if num is not None else None
                # Record cell blanks separately from nonnumeric numeric-channel cells and numeric zeros.
                missing = values.isna() | values.astype("string").str.strip().eq("").fillna(False)
                fields.append(identity | dict(
                    field=field, signal=code, meaning=meaning, unit=unit, label=label,
                    inferred_dtype=str(values.dtype), rows=len(frame),
                    blank_cells=int(missing.sum()),
                    blank_rate=float(missing.mean()) if len(frame) else np.nan,
                    finite_numeric=int(len(finite)) if finite is not None else None,
                    nonnumeric_nonblank=int((~missing & num.isna()).sum()) if code and num is not None else None,
                    zeros=int(finite.eq(0).sum()) if finite is not None else None,
                    min=finite.min() if finite is not None else np.nan,
                    median=finite.median() if finite is not None else np.nan,
                    max=finite.max() if finite is not None else np.nan,
                    wc_outside_range=int((~finite.between(0, 100)).sum()) if code == "wc" else None,
                ))
                if label and code not in {"", "time", "voltage"}:
                    if code in by_signal.setdefault(label, {}):
                        raise ValueError(f"Duplicate signal definition: {name} {sheet} {label} {code}")
                    by_signal[label][code] = field
            if kind != "sensor":
                continue
            family = logical_family(year, [label for label, m in by_signal.items() if "wc" in m])
            for label, columns in by_signal.items():
                mapping.extend(identity | dict(family=family, channel_label=label, signal=code,
                                               field=field, hardware_serial="not released")
                               for code, field in columns.items())
                if source["exact_file_duplicate"] or "wc" not in columns:
                    continue
                data = frame[list(columns.values())].rename(columns={v: k for k, v in columns.items()})
                data = data.apply(pd.to_numeric, errors="coerce")
                data = data.assign(time=ts, year=year, family=family, channel=label,
                                   member=name, sheet=sheet, source_row=frame.index.to_numpy() + 2)
                records.append(data)
        if kind == "sensor":
            order = pd.concat(order_times, ignore_index=True)
            file_rows.append(dict(member=name, year=year, file_duplicate=source["exact_file_duplicate"],
                                  sheets=len(order_times), **cadence(order)))
    save("sheet_audit", sheets)
    save("field_audit", fields)
    save("export_channel_mapping", mapping)
    save("file_clock_audit", file_rows)
    return pd.concat(records, ignore_index=True)


def audit_channels(raw):
    stream = raw.loc[raw.time.notna()].copy()
    stream["slot"] = stream.time.dt.floor("30min")
    stream["phase_s"] = (stream.time - stream.slot).dt.total_seconds()
    streams, gap_rows, slots, duplicate_slots, source_pairs = [], [], {}, [], []
    columns = ["wc", "soil_temp", "air_temp", "air_rh", "co2", "light"]
    signals, signal_gaps = [], []
    for (year, family, channel), rows in stream.groupby(["year", "family", "channel"], sort=True):
        base = dict(year=year, family=family, channel=channel)
        start, stop = map(pd.Timestamp, WINDOWS[year])
        r = rows.loc[rows.time.ge(start) & rows.time.lt(stop)]
        for scope, segment in [("full_export", rows), ("activity", r)]:
            data = segment.loc[segment.wc.between(0, 100)].copy()
            near = data.loc[data.phase_s <= 600]
            unique = near.drop_duplicates(["time", *columns])
            multi = unique.groupby("slot").size()
            colliding = multi[multi > 1]
            # Separate byte-identical re-exports from same-slot data with different source timestamps or values.
            for slot, d in unique.loc[unique.slot.isin(colliding.index)].groupby("slot"):
                duplicate_slots.append(base | dict(
                    scope=scope, slot=slot, distinct_rows=len(d), wc_min=d.wc.min(), wc_max=d.wc.max(),
                    distinct_wc=d.wc.nunique(), distinct_times=d.time.nunique(),
                    sources=" | ".join(sorted(d.member.unique())),
                ))
            chosen = unique.loc[~unique.slot.isin(colliding.index)].sort_values("slot")
            expected = len(pd.date_range(start, stop, freq="30min", inclusive="left")) if scope == "activity" else (
                int((chosen.slot.max() - chosen.slot.min()) / pd.Timedelta(minutes=30)) + 1 if len(chosen) else 0)
            c = cadence(data.time)
            wc = data.wc
            streams.append(base | dict(
                scope=scope, source_rows=len(segment), finite_in_range=len(data),
                blank_or_nonfinite_wc=int((~np.isfinite(segment.wc)).sum()),
                wc_outside_range=int((np.isfinite(segment.wc) & ~segment.wc.between(0, 100)).sum()),
                wc_min=wc.min(), wc_median=wc.median(), wc_max=wc.max(), wc_zero=int(wc.eq(0).sum()),
                qx_joint_zero_slots=int(chosen[["air_temp","air_rh","co2"]].eq(0).all(axis=1).sum()),
                xy_joint_zero_slots=int(chosen[["wc","soil_temp"]].eq(0).all(axis=1).sum()),
                outside_phase_0_10min=len(data)-len(near), exact_rows_collapsed=len(near)-len(unique),
                ambiguous_slots=len(colliding), ambiguous_rows=int(colliding.sum()),
                valid_slots=len(chosen), expected_slots=expected,
                slot_missing_rate=1-len(chosen)/expected if expected else np.nan,
                coincident_wc_soil_temp_air_temp_air_rh=int(chosen[["wc","soil_temp","air_temp","air_rh"]]
                                                           .apply(np.isfinite).all(axis=1).sum()),
            ) | c)
            if scope == "activity":
                slots[(year, family, channel)] = chosen
                have = pd.DatetimeIndex(chosen.slot)
                want = pd.date_range(start, stop, freq="30min", inclusive="left")
                absent = ~want.isin(have)
                edges = np.diff(np.r_[False, absent, False].astype(np.int8))
                for i, j in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
                    gap_rows.append(base | dict(first_slot=want[i], last_slot=want[j-1],
                                                missing_slots=j-i, minutes=30*(j-i),
                                                boundary=i==0 or j==len(want)))
                probe_zero = chosen[["wc", "soil_temp"]].eq(0).all(axis=1)
                weather_zero = chosen[["air_temp", "air_rh", "co2"]].eq(0).all(axis=1)
                for signal in columns:
                    has_value = np.isfinite(chosen[signal])
                    has_value &= ~probe_zero if signal in {"wc", "soil_temp"} else ~weather_zero
                    absent_signal = ~want.isin(chosen.loc[has_value, "slot"])
                    signals.append(base | dict(signal=signal, expected_slots=len(want),
                                               unavailable_slots=int(absent_signal.sum()),
                                               unavailable_rate=absent_signal.mean()))
                    edges_signal = np.diff(np.r_[False, absent_signal, False].astype(np.int8))
                    for i, j in zip(np.flatnonzero(edges_signal == 1), np.flatnonzero(edges_signal == -1)):
                        signal_gaps.append(base | dict(
                            signal=signal, first_slot=want[i], last_slot=want[j-1],
                            missing_slots=j-i, minutes=30*(j-i), boundary=i==0 or j==len(want)))
        group_by_source = rows.groupby("member", sort=True)
        for (a, left), (b, right) in combinations(group_by_source, 2):
            l = left.drop_duplicates(["time", *columns]).drop_duplicates("slot", keep=False)
            rr = right.drop_duplicates(["time", *columns]).drop_duplicates("slot", keep=False)
            shared = l.merge(rr, on="slot", suffixes=("_a", "_b"))
            if len(shared):
                source_pairs.append(base | dict(
                    member_a=a, member_b=b, shared_slots=len(shared),
                    exact_times=int(shared.time_a.eq(shared.time_b).sum()),
                    wc_agree_0_01pp=int((abs(shared.wc_a-shared.wc_b) <= .01).sum()),
                    wc_nonmissing_both=int((shared.wc_a.notna() & shared.wc_b.notna()).sum()),
                    wc_abs_difference_median=abs(shared.wc_a-shared.wc_b).median(),
                    all_six_agree_0_0001=int(np.logical_and.reduce([
                        abs(shared[f"{col}_a"] - shared[f"{col}_b"]) <= .0001 for col in columns
                    ]).sum()),
                ))
    alignment = []
    for ((year, family, channel), a), ((yr, fam, ch), b) in combinations(slots.items(), 2):
        if year != yr:
            continue
        joined = a[["slot","time","wc"]].merge(b[["slot","time","wc"]], on="slot", suffixes=("_a","_b"))
        offset = (joined.time_b - joined.time_a).dt.total_seconds()
        diff = abs(joined.wc_a-joined.wc_b)
        alignment.append(dict(
            year=year, family_a=family, channel_a=channel, family_b=fam, channel_b=ch,
            slots_a=len(a), slots_b=len(b), shared_slots=len(joined), exact_clock_matches=int(offset.eq(0).sum()),
            offset_s_median=offset.median(), abs_offset_s_p95=abs(offset).quantile(.95),
            wc_abs_difference_median=diff.median(), wc_agree_0_01pp=int((diff <= .01).sum()),
        ))
    save("sensor_stream_audit", streams)
    save("observed_gap_runs", gap_rows)
    save("signal_availability", signals)
    save("signal_gap_runs", signal_gaps)
    save("cross_channel_alignment", alignment)
    save("overlapping_source_members", source_pairs)
    save("ambiguous_slots", duplicate_slots)
    return slots


def main():
    sources, contents = inventory()
    save("source_manifest", sources, top_level=True)
    raw = audit_tables(sources, contents)
    slots = audit_channels(raw)
    print(f"{len(sources)} source members verified; {len(slots)} season/family/channel streams")


if __name__ == "__main__":
    main()
