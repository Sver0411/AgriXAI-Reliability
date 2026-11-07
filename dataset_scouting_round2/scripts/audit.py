"""Profile the downloaded observations without fitting models or altering source files."""
import hashlib
import io
import json
import re
import csv
import zlib
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
OUT = ROOT / "statistics"
NA_VALUES = ["", "NULL", "null", "NA", "N/A", "NaN", "nan", "#N/A", "#VALUE!"]
TIME_KEYS = ["ts_generation", "datetime_start", "datetime", "timestamp", "timestep", "date_time", "time", "date", "日期", "时间", "采集时间", "数据时间"]
SENSOR_KEYS = ["generated_by", "device_identifier", "node", "sensor_id", "line", "Plant_ID"]
TARGETS = {
    "evolving": r"^stem_size$|^fruit_size$|^leaf_temperature$|^water_potential$|^humidity$|^current_volume$|^valve_state$|^gdd$",
    "irrigation": r"^humidity$|^current_volume$|^gdd$",
    "horti": r"土壤水分|茎粗|叶面积|株高|产量|^NDVI$|^LAI$|^LNC$|^LDW$",
    "electro": r"^class$|^CH[12]$",
    "mobile": r"Harvest|Chlorophyll|Sap pH|Moisture",
    "micro": r"diameter|weight|growth|length|firmness",
    "lettuce": r"^B\d+\s*\(g\)|Volume|^EC ",
    "wp_protocol": r"water.potential|lwp|swp|transpiration|^tr$",
    "wp_sensors": r"water_potential_mean",
    "wur": r"^WC_slab[12]$|^Stem_thick$|^Stem_elong$|^Prod[AB]$|^Cum_irr$",
}


def write(name, rows):
    pd.DataFrame(rows).to_csv(OUT / (name + ".csv"), index=False, encoding="utf-8-sig")


def read_csv(data, path):
    codec = "utf-8-sig"
    try:
        string = data.decode(codec)
    except UnicodeDecodeError:
        codec = "gb18030"
        string = data.decode(codec)
    first = string.splitlines()[0]
    separator = ";" if first.count(";") > first.count(",") else ","
    try:
        df = pd.read_csv(io.StringIO(string), sep=separator, low_memory=False, skipinitialspace=True,
                         keep_default_na=False, na_values=NA_VALUES)
    except pd.errors.ParserError as error:
        raise ValueError(f"Cannot parse {path}: {error}") from error
    return df, f"{codec}; delimiter {separator!r}"


def tables(data, path):
    extension = Path(path).suffix.lower()
    if extension == ".csv":
        df, codec = read_csv(data, path)
        yield "(CSV)", df, codec
    elif extension == ".xlsx":
        book = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        for sheet in book:
            rows = sheet.values
            header = next(rows, None)
            if header is None:
                continue
            names = []
            for index, cell in enumerate(header, 1):
                field = str(cell).strip() if cell is not None else f"(unnamed {index})"
                names.append(field if field not in names else f"{field} [column {index}]")
            df = pd.DataFrame.from_records(rows, columns=names).dropna(how="all")
            keep = [c for c in df if not c.startswith("(unnamed ") or df[c].notna().any()]
            yield sheet.title, df[keep], "Excel cached values, first row as header"
        book.close()
    elif extension == ".docx":
        # The Horti supplement has Word data tables. Keep title text out of row counts.
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        with ZipFile(io.BytesIO(data)) as z:
            document = ET.fromstring(z.read("word/document.xml"))
        for index, node in enumerate(document.findall(".//w:tbl", ns), 1):
            rows = [["".join(t.text or "" for t in cell.findall(".//w:t", ns))
                     for cell in row.findall("./w:tc", ns)] for row in node.findall("./w:tr", ns)]
            if not rows:
                continue
            width = max(map(len, rows))
            header = [(f"{i+1}: {rows[0][i]}" if i < len(rows[0]) else str(i+1)) for i in range(width)]
            yield f"Word table {index}", pd.DataFrame([r + [None]*(width-len(r)) for r in rows[1:]], columns=header), "Word table cells; merged layout retained"


def data_sources():
    for group in ("evolving", "irrigation", "micro", "wp_protocol", "strawberry"):
        for archive in sorted((RAW/group).glob("*.zip")):
            with ZipFile(archive) as z:
                for item in z.infolist():
                    name = item.filename
                    if not item.is_dir():
                        path = name.partition("/")[2] if group in {"evolving", "irrigation", "micro"} else name
                        yield group, path, z.read(item)
    for group in ("horti", "electro", "mobile", "lettuce", "wp_sensors"):
        for path in sorted((RAW/group).rglob("*")):
            if path.is_file():
                yield group, path.relative_to(RAW/group).as_posix(), path.read_bytes()
    for path in sorted((RAW/"wur"/"extracted").rglob("*")):
        if path.is_file():
            yield "wur", path.relative_to(RAW/"wur"/"extracted").as_posix(), path.read_bytes()


def profile_data(group, path):
    if group == "micro":
        return path.startswith("PlantData/") or "/central.xlsx" in path.lower()
    if group == "electro":
        return path.endswith(".csv") and path.startswith("zip_members/")
    if group == "wp_protocol":
        return Path(path).name in {"LWP_raw.csv","SWP_raw.csv","W_raw.csv","Tr_raw.csv","Plants_ID.csv"}
    return Path(path).stem.lower() != "info"


def clock(df, group, path):
    cols = {str(c).strip().lower(): c for c in df}
    key = next((cols[n] for n in TIME_KEYS if n in cols), None)
    if group == "wur" and "%time" in cols:
        key = cols["%time"]
        serial = pd.to_numeric(df[key],errors="coerce")
        # Eight short Reference/TomQuality offsets have no verified origin.
        parsed = pd.to_datetime(serial.where(serial >= 40000),unit="D",origin="1899-12-30",errors="coerce")
        return parsed, key, "Excel serial date; short unanchored day codes unresolved"
    if group == "evolving" and key is None:
        raise ValueError(path)
    if group == "irrigation" and Path(path).name == "indicators.csv":
        key = df.columns[0]
    if group == "wp_sensors" and {"date","time"} <= set(cols):
        day = pd.to_datetime(df[cols["date"]], errors="coerce")
        val = day.dt.strftime("%Y-%m-%d") + " " + df[cols["time"]].astype("string")
        return pd.to_datetime(val, format="mixed", errors="coerce"), f"{cols['date']} + {cols['time']}", "source calendar + clock, offset unresolved"
    if key is not None:
        values = df[key]
        if key == "ts_generation" or (group == "irrigation" and Path(path).name == "indicators.csv"):
            return pd.to_datetime(pd.to_numeric(values, errors="coerce"), unit="ms", utc=True, errors="coerce"), key, "Unix ms UTC"
        if pd.api.types.is_numeric_dtype(values):
            return None, key, "numeric offset/index; not resolved"
        parsed = pd.to_datetime(values, format="mixed", errors="coerce", dayfirst=group == "mobile")
        return parsed, key, "source clock without confirmed offset"
    if group == "horti" and ("grow_index" in path or "growth_index" in path):
        match = re.search(r"/(20\d{6})\.csv$", path)
        if match:
            day = pd.to_datetime(match[1], format="%Y%m%d")
            return pd.Series(day, index=df.index), "(filename day)", "file day; clock unavailable"
    return None, "", "absolute time not confirmed"


def unique_intervals(time):
    idx = pd.DatetimeIndex(time.dropna().unique()).sort_values().as_unit("ns")
    seconds = np.diff(idx.asi8) / 1e9
    data = dict(n_times=len(idx), first=str(idx[0]) if len(idx) else "", last=str(idx[-1]) if len(idx) else "",
                n_calendar_days=len(idx.normalize().unique()), n_positive_intervals=len(seconds))
    if len(seconds):
        rounded = np.round(seconds, 3)
        mode, _ = Counter(rounded).most_common(1)[0]
        data.update(min_s=float(seconds.min()), median_s=float(np.median(seconds)), max_s=float(seconds.max()),
                    mean_s=float(seconds.mean()), std_s=float(seconds.std()), mode_ms_rounded_s=float(mode),
                    p05_s=float(np.quantile(seconds,.05)), p95_s=float(np.quantile(seconds,.95)),
                    gaps_gt_1h=int((seconds > 3600).sum()), gaps_gt_24h=int((seconds > 86400).sum()),
                    rounded_seconds_distribution=json.dumps({int(k):v for k,v in sorted(Counter(np.rint(seconds)).items())}))
    return data


def field_info(group, path, field, time_field):
    name = field.lower()
    f = Path(path).stem.lower()
    if field == time_field:
        return "timestamp", "as recorded; see time_basis"
    if name in SENSOR_KEYS or name in {"plant_id","pid","plant","编号","处理","treatment","day"}:
        return "record or group key", ""
    if group == "evolving" or group == "irrigation":
        known = {
            "co2": ("CO2", "ppm"), "electrical_conductivity": ("soil conductivity", "µS/cm"),
            "temperature": ("soil temperature" if "soil" in f else "air temperature", "°C"),
            "humidity": ("soil volumetric moisture" if "soil" in f else "air relative humidity", "%"),
            "water_temperature": ("water temperature", "°C"), "ambient_temperature": ("ambient temperature", "°C"),
            "current_volume": ("cumulative water meter", "m³ as documented; scale changes require checking"),
            "water_potential": ("soil matric potential; sensor reading", "kPa"),
            "leaf_temperature": ("leaf surface temperature sensor", "°C"),
            "stem_size": ("dendrometer reading; observed range conflicts with README specification", "mm as documented"),
            "fruit_size": ("fruit dendrometer reading", "mm"), "valve_state": ("actuator state", "0/1"),
            "pressure": ("ambient or line pressure; see table", "hPa for ambient; see README for line"),
        }
        return known.get(name, ("daily derived agronomic index" if "indicators" in f else field, "source README"))
    if group == "electro":
        if name in {"ch1","ch2"}:
            return "plant electrical potential after release window/downsampling", "mV (paper)"
        if name == "class":
            return "treatment-phase operational class", "0/1 as published"
        if name.startswith("value__"):
            return "derived tsfresh window statistic", "feature-specific"
    if group == "wur":
        if name.startswith("wc_slab"):
            return "measured rockwool slab water content", "%"
        known = {"tair":("indoor air temperature","°C"),"rhair":("indoor relative humidity","%"),
                 "cum_irr":("daily irrigation accumulation; resets daily, calculated","L/m²"),
                 "tot_par":("computed indoor total PAR","µmol/(m²·s)"),"stem_thick":("weekly sample-stem mean thickness","mm"),
                 "stem_elong":("weekly sample-stem mean elongation","cm/week")}
        if name in known:
            return known[name]
    if group == "horti":
        known = [("土壤水分", "substrate moisture", "%"),("土壤温度","substrate temperature","°C"),
                 ("空气温度","air temperature","°C"),("相对湿度","air relative humidity","%"),
                 ("二氧化碳","carbon dioxide","ppm"),("光照","illuminance","klux"),
                 ("茎粗","manual stem diameter","mm, verify sheet protocol"),("株高","manual plant height","cm, verify sheet protocol"),
                 ("叶面积","leaf area","source protocol"),("电压","supply voltage","V")]
        for prefix, meaning, unit in known:
            if field.startswith(prefix):
                return meaning, unit
    return field, "see source header or protocol"


def profile(group, path, sheet, df, codec, state):
    if df.empty:
        return
    df = df.replace(r"^\s*$", np.nan, regex=True).dropna(how="all")
    time, time_field, clock_basis = clock(df, group, path)
    keys = [c for c in SENSOR_KEYS if c in df]
    group_key = keys[:1] if not ("line" in keys and len(keys) > 1) else [keys[0], "line"]
    cohort = ((key, subset.index) for key, subset in df.groupby(group_key, dropna=False, sort=False)) if group_key else [("(table)", df.index)]
    detail = dict(dataset=group, member=path, sheet=sheet)
    info = dict(detail, rows=len(df), fields=len(df.columns), read_format=codec, time_field=time_field,
                clock_basis=clock_basis, duplicate_rows=int(df.duplicated().sum()))
    if time is not None:
        info.update(time_unparsed=int(time.isna().sum()), **unique_intervals(time))
        for key, indexes in cohort:
            t = time.loc[indexes].dropna()
            state["cadence"].append(dict(detail, group_by="+".join(group_key), group=str(key), time_basis=clock_basis,
                                         rows=len(indexes), valid_time_rows=len(t), duplicate_time_extra=int(t.duplicated().sum()),
                                         backward_steps=int((t.diff().dt.total_seconds() < 0).sum()), **unique_intervals(t)))
    state["profiles"].append(info)
    for field in df:
        values = df[field]
        meaning, units = field_info(group, path, field, time_field)
        numeric = pd.to_numeric(values, errors="coerce") if field != time_field else pd.Series(dtype=float)
        n = numeric[np.isfinite(numeric)]
        state["fields"].append(dict(detail, source_field=field, inferred_type=str(values.dtype), meaning=meaning,
                                    unit=units, rows=len(df), missing=int(values.isna().sum()),
                                    missing_rate=float(values.isna().mean()), unique_nonmissing=int(values.nunique())))
        if re.search(TARGETS.get(group, r"(?!)"), field, flags=re.I):
            stem_specification = int(n.between(5,25).sum()) if group == "evolving" and field == "stem_size" else None
            state["labels"].append(dict(detail, field=field, meaning=meaning, units=units, nonmissing=int(values.count()),
                                        finite_numeric=len(n), min=n.min(), median=n.median(), max=n.max(),
                                        zeros=int((n == 0).sum()), p05=n.quantile(.05), p95=n.quantile(.95),
                                        stem_within_documented_5_25_mm=stem_specification,
                                        value_counts=json.dumps({str(k):int(v) for k,v in values.value_counts().items()}, ensure_ascii=False)
                                        if values.nunique() <= 12 else ""))
    if group in {"evolving","irrigation"} and Path(path).suffix == ".csv":
        state["parma"][(group,Path(path).stem)] = df
    if group == "electro" and "02_test_train_val_split" in path:
        state["electro_splits"][Path(path).stem] = df[["plant_id","node","day","datetime_start","datetime_end","class"]]
    if group == "wur" and Path(path).stem in {"GrodanSens","GreenhouseClimate"} and time is not None:
        state["wur"][path] = df.assign(parsed_time=time)
    if group == "mobile":
        state["mobile"] = df.assign(parsed_date=time)
    if group == "horti" and ("grow_index" in path or "growth_index" in path):
        identifier = next((f for f in ("Pid","编号","Plant_ID") if f in df), None)
        if identifier and time is not None:
            traits = [f for f in ("茎粗","叶面积","株高","NDVI","LAI","LNC") if f in df]
            season = int(re.search(r"/(20\d{2})/",path)[1])
            state["horti_plants"].append(df[[identifier,*traits]].rename(columns={identifier:"plant_id"})
                                        .assign(date=time.dt.normalize(), campaign=season, member=path))
    if group == "micro" and "/central.xlsx" in path.lower() and time is not None:
        state["micro_central"][path] = df.assign(parsed_time=time)


def compare_parma(tables_by_key):
    rows = []
    for category, fields in (("environmental", ["co2","pressure","temperature","humidity"]),
                             ("water_meter",["line","current_volume"]),
                             ("soil",["line","electrical_conductivity","humidity","temperature"])):
        earlier = tables_by_key[("irrigation", f"stuard_{category}_data")].rename(columns={"device_identifier":"device"})
        later_name = f"{category}2023"
        later = tables_by_key[("evolving", later_name)].rename(columns={"generated_by":"device","device_identifier":"device"})
        cols = ["device","ts_generation",*fields]
        a,b = (table[cols].assign(**{f:pd.to_numeric(table[f],errors="coerce") for f in cols[1:]})
               .dropna(subset=["device","ts_generation"]).drop_duplicates()
               for table in (earlier,later))
        keys = cols[:2]
        rows.append(dict(category=category, older_rows=len(earlier), evolving_2023_rows=len(later),
                         older_distinct_rows=len(a), matching_device_time_and_values=len(a.merge(b, on=cols)),
                         shared_distinct_device_times=len(a[keys].drop_duplicates().merge(b[keys].drop_duplicates(), on=keys))))
    return rows


def causal_windows(by_key):
    """Screen 60-min-ahead soil-moisture targets with past-only sensors. No resampling."""
    report = []
    for year in (2023,2024,2025):
        def numeric_table(stem, columns):
            data = by_key["evolving",f"{stem}{year}"]
            numeric = {f: pd.to_numeric(data[f],errors="coerce") for f in ["ts_generation",*columns]}
            return data.assign(**numeric).dropna(subset=numeric.keys())
        soil = numeric_table("soil",["line","humidity"])
        weather = numeric_table("environmental",["temperature","humidity"])
        meter = numeric_table("water_meter",["line","current_volume"])
        devices = "generated_by"
        lo, hi = weather.ts_generation.min(), weather.ts_generation.max()
        weather_time = np.sort(weather.ts_generation.unique())
        soil = soil[soil.ts_generation.between(lo,hi) & soil.humidity.between(0,100)]
        for (line, device), df in soil.groupby(["line",devices], sort=True):
            if pd.isna(line):
                continue
            t = np.sort(df.ts_generation.unique())
            m = np.sort(meter.loc[meter.line.eq(line),"ts_generation"].unique())
            # At least six actual readings spanning 40–75 min, with no >20 min gap.
            gaps = np.diff(t)
            prev = np.arange(len(t))
            eligible = prev >= 5
            eligible[5:] &= (t[5:]-t[:-5] >= 2400000) & (t[5:]-t[:-5] <= 4500000)
            for step in range(5):
                eligible[5:] &= gaps[step:len(t)-5+step] <= 1200000
            future = np.searchsorted(t, t+3600000)
            valid = future < len(t)
            valid[valid] &= t[future[valid]] <= t[valid]+4200000
            def prior(values):
                ix = np.searchsorted(values, t, side="right")-1
                available = ix >= 0
                available[available] &= t[available]-values[ix[available]] <= 1200000
                return available
            usable = eligible & valid & prior(weather_time) & prior(m)
            report.append(dict(campaign=year,line=line,device=device,soil_rows=len(df),unique_target_timestamps=len(t),
                               candidate_same_probe_after_60_70m=int(valid.sum()), usable_windows=int(usable.sum()),
                               usable_distinct_days=len(pd.DatetimeIndex(pd.to_datetime(t[usable],unit="ms",utc=True)).normalize().unique()),
                               first_input=str(pd.to_datetime(t[usable][0],unit="ms",utc=True)) if usable.any() else "",
                               last_input=str(pd.to_datetime(t[usable][-1],unit="ms",utc=True)) if usable.any() else ""))
    return report


def group_evidence(state):
    labels = []
    splits = state["electro_splits"]
    for fold, df in sorted(splits.items()):
        for (plant, node, label), items in df.groupby(["plant_id","node","class"],dropna=False):
            labels.append(dict(fold=fold, plant_id=plant, node=node, label=label, rows=len(items),
                               distinct_days=items.day.nunique(), first=items.datetime_start.min(), last=items.datetime_end.max(),
                               same_plant_in_other_folds="|".join(k for k in sorted(splits) if k != fold and plant in set(splits[k].plant_id)),
                               same_node_in_other_folds="|".join(k for k in sorted(splits) if k != fold and node in set(splits[k].node))))
    write("electro_30min_split_labels", labels)
    soil23, soil24 = (state["parma"][("evolving",f"soil{year}")] for year in (2023,2024))
    keys = ["generated_by","ts_generation","line","electrical_conductivity","humidity","temperature"]
    a,b = (df[keys].assign(**{k:pd.to_numeric(df[k],errors="coerce") for k in keys[1:]})
           .dropna(subset=keys[:2]).drop_duplicates() for df in (soil23,soil24))
    grouped = b.assign(year=pd.to_datetime(b.ts_generation,unit="ms",utc=True).dt.year).groupby("year")
    write("evolving_soil2024_calendar_overlap", [dict(actual_year=y,distinct_rows=len(df),
                                                     exact_match_to_soil2023=len(df.merge(a,on=keys))) for y,df in grouped])
    if state["horti_plants"]:
        growth = pd.concat(state["horti_plants"], ignore_index=True)
        units = []
        for year, df in growth.groupby("campaign"):
            good = df.dropna(subset=["date","plant_id"]).copy()
            good["plant_id"] = good.plant_id.astype(str).str.strip()
            key = ["date","plant_id"]
            cleaned = good[~good.plant_id.isin({"","nan","None"})]
            r = dict(campaign=year, raw_rows=len(df), plant_ids=cleaned.plant_id.nunique(), dates=cleaned.date.nunique(),
                     first=str(cleaned.date.min()), last=str(cleaned.date.max()),
                     plant_day_unique=len(cleaned[key].drop_duplicates()))
            for trait in ("茎粗","叶面积","株高","NDVI","LAI","LNC"):
                r[f"{trait}_finite_plant_days"] = len(cleaned.loc[np.isfinite(pd.to_numeric(cleaned[trait],errors="coerce")),key].drop_duplicates())
            units.append(r)
        write("horti_plant_day_counts",units)
        counts = []
        for (year,day),df in growth.groupby(["campaign","date"]):
            row = dict(campaign=year,date=day.date(),source_rows=len(df),plant_ids=df.plant_id.nunique())
            for trait in ("茎粗","叶面积","株高","NDVI","LAI","LNC"):
                row[trait] = df.loc[np.isfinite(pd.to_numeric(df[trait],errors="coerce")),"plant_id"].nunique()
            counts.append(row)
        write("horti_measurement_days",counts)
    mobile = state["mobile"]
    write("mobile_plants", [dict(plant=plant, rows=len(df), dates=df.parsed_date.nunique(), treatment_count=df.Treatment.nunique())
                            for plant,df in mobile.groupby("Plant")])


def compare_belgium(central):
    prior = ROOT.parent / "greenhouse_tomato_data_audit/raw/9hyv69sm82_v1.zip"
    if not prior.exists():
        return
    output = []
    with ZipFile(prior) as z:
        for year in (2019,2020):
            match = next(n for n in z.namelist() if n.endswith(f"/Environment/{year}/Central_{year}.xlsx"))
            _, older, _ = next(tables(z.read(match), match))
            rhs = older[["Time","T","RH"]].rename(columns={"Time":"parsed_time"})
            path = f"Environment/T&RH/{year}/CENTRAL.xlsx"
            lhs = central[path].rename(columns={"Temperature":"T","RelativeHumidity":"RH"})[["parsed_time","T","RH"]]
            compared = lhs.merge(rhs,on="parsed_time",suffixes=("_micro","_prior"),validate="one_to_one")
            same = [np.isclose(pd.to_numeric(compared[f+"_micro"],errors="coerce"),
                               pd.to_numeric(compared[f+"_prior"],errors="coerce"),
                               rtol=0,atol=1e-8,equal_nan=True) for f in ("T","RH")]
            output.append(dict(year=year, micro=path, round1=match, micro_rows=len(lhs), round1_rows=len(rhs),
                               shared_timestamps=len(compared), both_values_equal_with_shared_blanks=int((same[0]&same[1]).sum())))
    write("belgium_overlap",output)


def wur_windows(frames):
    rows = []
    for path in sorted(p for p in frames if p.endswith("/GrodanSens.csv")):
        compartment = path.split("/")[0]
        soil = frames[path]
        climate = frames[compartment + "/GreenhouseClimate.csv"]
        weather = climate[["parsed_time","Tair","Rhair","Cum_irr"]].dropna()
        weather_index = pd.DatetimeIndex(weather.parsed_time).sort_values().as_unit("ns")
        for column in ("WC_slab1","WC_slab2"):
            values = pd.to_numeric(soil[column],errors="coerce")
            valid = soil.loc[values.between(0,100),"parsed_time"]
            times = pd.DatetimeIndex(valid.dropna().unique()).sort_values().as_unit("ns")
            t = times.asi8
            step = 60 * 10**9
            start = np.arange(len(t))
            eligible = start >= 11
            eligible[11:] &= (t[11:]-t[:-11] >= 45*step) & (t[11:]-t[:-11] <= 75*step)
            gaps = np.diff(t)
            for k in range(11):
                eligible[11:] &= gaps[k:len(t)-11+k] <= 10.1*step
            target = np.searchsorted(t,t+59.98*step)
            target_ok = target < len(t)
            target_ok[target_ok] &= t[target[target_ok]] <= t[target_ok]+65.02*step
            idx = weather_index.searchsorted(times,side="right")-1
            aligned = idx >= 0
            aligned[aligned] &= t[aligned]-weather_index.asi8[idx[aligned]] <= 10.1*step
            good = eligible & target_ok & aligned
            rows.append(dict(compartment=compartment,channel=column,source_rows=len(soil),
                             finite_within_0_100=len(valid),candidate_windows=int(good.sum()),
                             candidate_days=len(times[good].normalize().unique()),
                             **unique_intervals(valid)))
    if rows:
        write("wur_substrate_windows",rows)


def verify_downloads():
    catalog = RAW / "catalog"
    checked = []
    for group in ("horti","electro","lettuce"):
        picks = set((catalog / f"{group}_selection.txt").read_text(encoding="utf-8-sig").splitlines())
        with (catalog / f"{group}_zip_members.csv").open(encoding="utf-8-sig",newline="") as file:
            selected = {row["member"]:row for row in csv.DictReader(file) if row["member"] in picks}
        if len(selected) != len(picks):
            raise ValueError(f"Missing ZIP directory entries for {group}")
        for member in sorted(picks):
            dest = RAW/group/"zip_members"/Path(*(part.replace("%","%25").replace(":","%3A") for part in member.split("/")))
            data = dest.read_bytes()
            ref = selected[member]
            if len(data) != int(ref["bytes"]) or f"{zlib.crc32(data):08x}" != ref["crc32"]:
                raise ValueError(f"Remote ZIP member differs locally: {member}")
    official_md5 = {}
    for group in ("electro","mobile","strawberry","wp_protocol","wp_sensors"):
        meta = json.loads((catalog/f"{group}.json").read_text(encoding="utf-8-sig"))
        for ref in meta["files"]:
            official_md5[group,ref["key"]] = ref["checksum"].removeprefix("md5:")
    meta = json.loads((catalog/"wur.json").read_text(encoding="utf-8-sig"))
    for ref in meta["files"]:
        official_md5["wur",ref["name"]] = ref["supplied_md5"]
    for group in ("evolving","irrigation","micro","electro","mobile","strawberry","wp_protocol","wp_sensors","wur"):
        for p in sorted((RAW/group).iterdir()):
            if not p.is_file():
                continue
            data = p.read_bytes()
            md5 = hashlib.md5(data).hexdigest()
            expected = official_md5.get((group,p.name),"")
            if expected and md5 != expected:
                raise ValueError(f"Official MD5 mismatch: {p}")
            checked.append(dict(dataset=group,file=p.relative_to(ROOT).as_posix(),bytes=len(data),
                                sha256=hashlib.sha256(data).hexdigest(),md5=md5,official_md5=expected))
    write("source_archive_checksums",checked)


def main():
    OUT.mkdir(exist_ok=True)
    verify_downloads()
    state = {n:[] for n in ("inventory","profiles","fields","cadence","labels","horti_plants")}
    state.update(parma={},electro_splits={},micro_central={},mobile=None,wur={})
    for group,path,data in data_sources():
        audit = profile_data(group,path) and Path(path).suffix.lower() in {".csv",".xlsx",".docx"}
        state["inventory"].append(dict(dataset=group,member=path,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),table_profiled=audit))
        if audit:
            for sheet,df,codec in tables(data,path):
                profile(group,path,sheet,df,codec,state)
    for key,name in {"inventory":"raw_file_inventory","profiles":"table_profiles","fields":"column_dictionary",
                     "cadence":"sampling_by_stream","labels":"raw_label_distribution"}.items():
        write(name,state[key])
    write("parma_overlap",compare_parma(state["parma"]))
    write("evolving_causal_windows",causal_windows(state["parma"]))
    group_evidence(state)
    compare_belgium(state["micro_central"])
    wur_windows(state["wur"])
    totals = pd.DataFrame(state["profiles"]).groupby("dataset").agg(tables=("sheet","size"),rows=("rows","sum"))
    print(totals.to_string())


if __name__ == "__main__":
    main()
