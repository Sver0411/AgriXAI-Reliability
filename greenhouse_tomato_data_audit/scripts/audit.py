"""Audit the unchanged Mendeley v1 archive. No imputation or model fitting."""
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "raw/9hyv69sm82_v1.zip"
CATALOG = ROOT / "raw/mendeley_file_catalog.json"
EXPECTED_YEARS = range(2019, 2025)
MISSING = {"na", "n/a", "nan", "null", "#n/a", "#value!", "#div/0!", "#ref!", "#num!", "#name?", "#null!"}
FORMATS = [
    (r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", "%d/%m/%Y %H:%M", "minute"),
    (r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", "%d/%m/%Y %H:%M:%S", "second"),
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y", "day"),
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S", "second"),
]


def site(year):
    return "KU Leuven (Heverlee)" if year in (2021, 2022) else "Proefcentrum Hoogstraten (Meerle)"


def normalize(value):
    if isinstance(value, str):
        if not value.strip() or value.strip().lower() in MISSING:
            return None
    return value


def sheets(path, data):
    if path.suffix == ".csv":
        yield "(CSV)", list(csv.reader(io.StringIO(data.decode("utf-8-sig")))), {}
        return
    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    with ZipFile(io.BytesIO(data)) as xlsx:
        xml_paths = [n for n in xlsx.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        formulas = []
        for xml_path in xml_paths:
            by_col = Counter()
            with xlsx.open(xml_path) as xml:
                for _, node in ElementTree.iterparse(xml, events=("end",)):
                    tag = node.tag.rsplit("}", 1)[-1]
                    if tag == "c":
                        if any(child.tag.endswith("}f") for child in node):
                            by_col[re.match(r"[A-Z]+", node.attrib["r"]).group()] += 1
                        node.clear()
                    elif tag == "row":
                        node.clear()
            formulas.append(dict(by_col))
    for sheet, counts in zip(book, formulas, strict=True):
        yield sheet.title, list(sheet.values), counts
    book.close()


def frame(rows):
    header = rows[0]
    width = max(map(len, rows))
    keep = [i for i in range(width) if any(i < len(row) and row[i] not in ("", None) for row in rows)]
    names = [str(header[i]) if i < len(header) and header[i] not in (None, "") else f"(unnamed {i+1})" for i in keep]
    records = [
        [row[i] if i < len(row) else None for i in keep]
        for row in rows[1:] if any(v is not None and v != "" for v in row)
    ]
    return pd.DataFrame(records, columns=names), keep


def parse_time(series):
    date_obj = series.map(lambda v: isinstance(v, pd.Timestamp) or hasattr(v, "date"))
    times = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    times.loc[date_obj] = pd.to_datetime(series.loc[date_obj])
    strings = series.where(~date_obj).astype("string").str.strip()
    used = Counter()
    if date_obj.any():
        used["Excel datetime"] = int(date_obj.sum())
    for pattern, fmt, precision in FORMATS:
        matched = strings.str.fullmatch(pattern, na=False)
        if matched.any():
            times.loc[matched] = pd.to_datetime(strings.loc[matched], format=fmt, errors="coerce")
            used[fmt] = int(matched.sum())
    return times, used


def campaign(path):
    return 2019 if path.stem == "LA_2019" else int(path.parts[1])


def field_info(path, tab, field, time):
    year = campaign(path)
    f = field.strip()
    key = re.sub(r"[^a-z0-9]+", "_", f.lower()).strip("_") or "unnamed"
    unknown = (key, "未说明", f, "来源未明确", "")
    if field == time:
        return ("date" if f == "Date" and path.parts[0] == "Plant" else "timestamp",
                "日或日期时间", "记录日期或时间戳", "时间索引", "原始时间保留；解析状态见 timestamp_quality.csv")
    if f.startswith("(unnamed"):
        return (key, "未说明", "无标题的额外源列", "未知", "保留非空原始位置，不据其位置推定通道")
    if path.parts[0] == "Environment":
        if path.stem.startswith("RMI"):
            specs = {
                "FID": ("标识符", "站点与时间组成的记录键"),
                "the_geom": ("度", "气象站坐标 WKT"),
                "code": ("代码", "气象站 ID"),
                "temp": ("°C", "室外空气温度"),
                "temp_min": ("°C", "最低室外空气温度"),
                "temp_max": ("°C", "最高室外空气温度"),
                "temp_grass_min": ("°C", "草面最低温度"),
                "humidity_relative": ("%", "室外相对湿度"),
                "pressure": ("hPa", "海平面气压"),
                "pressure_station_level": ("hPa", "站点气压"),
                "precip_quantity": ("mm", "降水量"),
                "precip_range": ("代码", "降水统计时段；1/2/4 表示过去 6/12/24 h"),
                "wind_speed": ("依单位码", "风速"),
                "wind_speed_unit": ("代码", "风速单位码"),
                "wind_direction": ("°", "风向"),
                "wind_peak_speed": ("m/s", "峰值风速"),
                "weather_current": ("代码", "现时天气"),
                "sun_duration_24hours": ("min", "24 小时日照时长"),
                "short_wave_from_sky_24hours": ("J/m²", "24 小时短波辐照量"),
                "cloudiness": ("okta / 特殊码", "云量"),
            }
            unit, meaning = specs.get(f, ("未说明", f))
            kind = "室外气象站观测/统计"
            if f in {"FID", "the_geom", "code", "wind_speed_unit"}:
                kind = "记录元数据"
            elif f in {"sun_duration_24hours", "short_wave_from_sky_24hours", "temp_min", "temp_max"}:
                kind = "室外衍生统计"
            return ("outdoor_" + key, unit, meaning, kind, "RMI SYNOP；24h 字段按观测计划发布")
        prefix, _, suffix = f.partition("_")
        channel = prefix.lower() + "_" if suffix and prefix in {"base", "top", "middle", "bottom"} else ""
        item = suffix if channel else f
        if item.lower() in {"t", "temperature"}:
            kind = "小时聚合环境观测" if tab == "GreenhouseClimateHourly" else "环境传感器读数"
            return (channel + "air_temperature", "°C", "空气温度", kind, "")
        if item.lower().replace(" ", "") in {"rh", "relativehumidity"}:
            kind = "小时聚合环境观测" if tab == "GreenhouseClimateHourly" else "环境传感器读数"
            return (channel + "relative_humidity", "%", "相对湿度", kind, "")
        if f == "G" or f.startswith("TotalIradiation"):
            return ("global_irradiance" + ("_top" if f.endswith("_top") else ""),
                    "W/m²", "总辐照度", "辐照度传感器读数", "")
        if f.startswith("PAR_"):
            return (key, "µmol/(m²·s)", "光合有效辐射通量密度", "PAR 传感器读数", "")
        if item == "Light" or f == "PYR":
            return (channel + key.removeprefix(channel), "未确认", "光传感器通道",
                    "环境传感器读数", "PYR 与工作表名 PAR 冲突，条带 Light 缺少列级单位；禁止等同 G 或 PAR")
        return unknown
    if path.stem.startswith("WP_") and re.fullmatch(r"WP[1-4]", f):
        return ("stem_water_potential_compartment_" + f[-1], "MPa", "对应冠层分层的茎水势",
                "压力室实测", "遮光包叶 30 min；Measurement 为测量编号，个体/叶片 ID 未发布")
    if f.startswith("Scale"):
        return ("load_cell_mass_" + f[-1], "g", "蒸腾试验称重通道的原始读数",
                "称重传感器读数", "未换算为蒸腾速率；不得把零当缺失")
    if f in {"Measurement", "Plant", "Plant ID", "Leaf", "Week", "Row", "Zone", "Side", "Fruit", "Position"}:
        meanings = {"Measurement": "测量/重复编号", "Plant": "表内植物标识", "Plant ID": "表内植物标识",
                    "Leaf": "表内叶标识", "Week": "周序号", "Row": "栽培行内位置", "Zone": "温室内部区域",
                    "Side": "采样侧；按各表原编码", "Fruit": "果位/果实编码", "Position": "果实采样位置"}
        return (key, "编号/类别", meanings[f], "采样设计元数据", "标识作用域限本表/活动，需核对才可跨表追踪")
    units = {
        "Days": "d（可能 DAA）", "DAA": "d", "Days after planting": "d", "Weeks after planting": "week",
        "Length": "cm", "Width": "cm", "Area": "cm²" if year == 2019 else "m²",
        "Mass": "g", "Fresh weight": "g", "Dry weight": "g", "Stem growth": "cm",
        "Total stem length": "cm", "Stem length": "cm" if year == 2021 else "m",
        "Stem diameter": "mm", "Diameter": "mm", "Diameter middle": "mm", "Diameter bottom": "mm",
        "Length between 5 leaves": "cm", "New stem": "cm", "FW": "%（见备注）", "DW": "%（见备注）",
        "Brix": "°Bx（字段推定）", "Brix 1": "°Bx（字段推定）", "Brix 2": "°Bx（字段推定）",
        "OP_Mpa": "MPa（字段）", "LAI": "无量纲", "Prunning": "类别",
    }
    descriptions = {
        "Days": "果实实验的日龄", "DAA": "开花后天数", "Days after planting": "定植后天数",
        "Weeks after planting": "定植后周数", "Length": "叶长", "Width": "叶宽", "Area": "叶面积",
        "Mass": "叶鲜重", "Fresh weight": "鲜重", "Dry weight": "干重", "Stem growth": "两次测量间主茎增量",
        "Total stem length": "主茎全长", "Stem length": "主茎长", "Stem diameter": "茎径", "Diameter": "果实最大直径",
        "Diameter middle": "带叶段中部茎径", "Diameter bottom": "带叶段底部茎径",
        "Length between 5 leaves": "跨五片叶的茎段长度", "New stem": "新增长茎长", "FW": "鲜重比例",
        "DW": "干重比例", "LAI": "叶面积指数", "Prunning": "修剪状态", "OP_Mpa": "果实渗透势换算字段",
        "OP": "果实渗透势相关汇总", "Osmotic p.1": "果实渗透势相关第 1 次读数",
        "Osmotic p.2": "果实渗透势相关第 2 次读数", "WP": "果实表中的 WP；协议未确认",
        "Ethylene": "乙烯相关列", "CO2": "二氧化碳相关列", "O2": "氧气相关列",
        "EthyleneRate": "乙烯速率列", "CO2Rate": "二氧化碳速率列", "O2Rate": "氧气速率列",
        "L": "果实颜色 L 相关", "a*": "果实颜色 a* 相关", "Firmness": "果实硬度",
    }
    if f.startswith("LA_comp"):
        return ("leaf_area_compartment_" + f[-1], "m²", "指定分层的叶面积", "植物性状观测", "")
    if f in {"Highest truss", "Initialized number of nodes (estimate)", "Leaves", "Flowers", "Trusses",
             "Number of leaves", "Number of trusses", "New leaves", "New flowers"}:
        return (key, "计数", f, "估计量" if "estimate" in f else "人工观测/计数", "")
    note = ""
    if f in {"FW", "DW"}:
        note = "2020 果实表非空值为 0–1 比例，Info 标为百分比；2021 源表按 0–100 表示"
    elif f == "Prunning":
        note = "空白语义不明，不自动解释为 No"
    elif f == "WP":
        note = "不得归入茎水势标签"
    kind = "植物性状观测"
    if f in {"FW", "DW", "LAI", "OP", "OP_Mpa", "DAA", "Days", "Days after planting", "Weeks after planting"} or f.endswith("Rate"):
        kind = "衍生量/日龄"
    elif f == "Prunning":
        kind = "人工状态记录"
    elif f not in units:
        kind = "测量/处理来源未确认"
    return (key, units.get(f, "未说明"), descriptions.get(f, f), kind, note)


def write_csv(name, records):
    pd.DataFrame(records).to_csv(ROOT / (name + ".csv"), index=False, encoding="utf-8-sig")


def true_runs(mask):
    ends = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    return zip(ends[::2], ends[1::2])


def tstr(t):
    return "" if pd.isna(t) else t.isoformat(sep=" ")


def datetime_set(times):
    return pd.DatetimeIndex(times.dropna().unique()).sort_values()


def intervals(ts):
    return np.diff(ts.asi8) / 1e9


def sampling_stats(ts):
    delta = intervals(ts)
    if not len(delta):
        return dict(n_times=len(ts), n_intervals=0)
    counts = Counter(delta)
    maximum = max(counts.values())
    modes = sorted(k for k, v in counts.items() if v == maximum)
    typical = float(np.median(delta))
    threshold = max(3 * typical, 60)
    return dict(n_times=len(ts), n_intervals=len(delta), min_seconds=min(delta),
                max_seconds=max(delta), mean_seconds=np.mean(delta), median_seconds=typical,
                mode_seconds="|".join(f"{x:g}" for x in modes), mode_count=maximum,
                std_seconds_population=np.std(delta), p05_seconds=np.quantile(delta, .05),
                p95_seconds=np.quantile(delta, .95), distinct_intervals=len(counts),
                gap_threshold_seconds=threshold, n_gaps=int(sum(delta > threshold)),
                n_gaps_gt_24h=int(sum(delta > 86400)),
                n_bins=json.dumps(dict(sorted(Counter(np.round(delta, 0)).items()))))


def before(index, query):
    positions = index.searchsorted(query, side="right") - 1
    good = positions >= 0
    lag = np.full(len(query), np.inf)
    lag[good] = (query.asi8[good] - index.asi8[positions[good]]) / 1e9
    return lag


def after(index, query):
    positions = index.searchsorted(query, side="left")
    good = positions < len(index)
    lag = np.full(len(query), np.inf)
    lag[good] = (index.asi8[positions[good]] - query.asi8[good]) / 1e9
    return lag


def stable_windows(index, events, step_s, minutes, lead):
    end = events - pd.Timedelta(minutes=lead)
    start = end - pd.Timedelta(minutes=minutes)
    ok = (before(index, end) <= step_s) & (before(index, start) <= step_s)
    for i in np.flatnonzero(ok):
        window = index[index.searchsorted(start[i], side="right") - 1:index.searchsorted(end[i], side="right")]
        ok[i] = bool(len(window) > 1 and max(intervals(window)) <= 2 * step_s)
    return ok


def file_role(path):
    stem = path.stem
    if stem == "Info":
        return "采样说明、字段单位、仪器或位置元数据"
    if stem.startswith("WP_"):
        return "压力室茎水势；2022 CSV/XLSX 与 2023/2024 重复比较见目标审查"
    if "fruit" in stem.lower():
        return "果实生长与化学；Fruit_2020 是两个 CSV 的整理候选"
    if stem.startswith("RMI"):
        return "温室外 SYNOP 气象站参照"
    if stem.startswith("Central"):
        return "温室中心气候；与其他通道及水势按时间核对"
    if stem.startswith(("S", "GB", "Light")) and path.parts[0] == "Environment":
        return "温室内微气候/光照通道；位置和仪器见同目录 Info"
    if stem.startswith("Transpiration"):
        return "短期称重试验原始称重值"
    return "植物形态、叶面积、重量或生长记录"


def official_catalog():
    folders = json.loads((ROOT / "raw/mendeley_folders.json").read_text(encoding="utf-8-sig"))
    by_id = {row["id"]: row for row in folders}
    def folder_path(key):
        if not key or key == "root":
            return Path()
        node = by_id[key]
        return folder_path(node.get("parent_id")) / node["name"]
    responses = json.loads(CATALOG.read_text(encoding="utf-8-sig"))
    return {(folder_path(group["folder_id"]) / entry["filename"]).as_posix(): entry
            for group in responses for entry in group["response"]}


def measurement_schema(rows, mode):
    if mode == "positions":
        return rows
    result = []
    for row in rows:
        if not any(v not in (None, "") for v in row):
            continue
        if mode == "rmi":
            result.append((row[0], row[1]))
        elif row[0] in {"Units", "Sensor types"}:
            result.append((row[0], None, None))
        else:
            result.append(tuple(row[:3]))
    return [("Entry", "Value")] + result if mode == "rmi" else [("Entry", "Description_or_Manufacturer", "Unit_or_Type")] + result


def wp_content(df, times):
    result = df[["Measurement", "WP1", "WP2", "WP3", "WP4"]].apply(pd.to_numeric, errors="raise")
    result.insert(0, "Time", times)
    return result.astype({c: float for c in result.columns if c != "Time"})


def audit():
    files, fields, misses, ts_quality, sampling, coverage = [], [], [], [], [], []
    missing_runs, missing_pairs, gaps, flat_runs = [], [], [], []
    streams, observed_times, wp, align, targets = {}, {}, {}, [], []
    reference_tables = {}
    catalog = official_catalog()
    archive_count = 0
    with ZipFile(ARCHIVE) as z:
        for name in sorted(z.namelist()):
            archive_count += 1
            path = Path(*Path(name).parts[1:])
            data = z.read(name)
            official = catalog.pop(path.as_posix())["content_details"]
            sha256 = hashlib.sha256(data).hexdigest()
            if len(data) != official["size"] or sha256 != official["sha256_hash"]:
                raise ValueError(f"Archive does not match the official file catalog: {path}")
            year = campaign(path)
            source = dict(file=path.as_posix(), site=site(year), campaign_year=year)
            metadata_rows = records = tab_count = 0
            for tab, rows, formulas in sheets(path, data):
                tab_count += 1
                metadata = path.stem == "Info" or tab == "Info"
                if metadata:
                    mode = "positions" if tab == "Position" else "rmi" if path.stem.startswith("RMI") else "info"
                    rows = measurement_schema(rows, mode)
                df, columns = frame(rows)
                if metadata:
                    metadata_rows += len(df)
                else:
                    records += len(df)
                details = dict(source, sheet=tab, role="metadata" if metadata else path.parts[0])
                time = next((x for x in ("Time", "Date", "timestamp") if x in df), None) if not metadata else None
                times, formats = parse_time(df[time]) if time else (pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]"), {})
                zone = ("UTC from FID +00 (cross-checked)" if path.stem == "RMI_2022" else
                        "unknown; offset absent" if time else "not applicable")
                fid_mismatch = None
                if path.stem == "RMI_2022":
                    fid_times = pd.to_datetime(df.FID.str.extract(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\+00")[0])
                    fid_mismatch = int(times.ne(fid_times).sum())
                    if fid_mismatch:
                        zone = "UTC indicated by FID; mismatch rows require review"
                precision = ("day only" if time == "Date" and path.parts[0] == "Plant" else
                             "minute display/export; duplicate instants" if path.stem.startswith("Transpiration") else
                             "absolute date-time where parseable" if time else
                             "days after planting only" if "Days after planting" in df else "none")
                valid = times.notna()
                if not metadata:
                    ts = datetime_set(times)
                    unparsed = df.loc[~valid, time] if time else pd.Series([], dtype=object)
                    ts_quality.append(dict(details, time_field=time, rows=len(df), parseable=int(valid.sum()),
                                           missing_time=int(unparsed.map(normalize).isna().sum()),
                                           invalid_nonblank=int(unparsed.map(normalize).notna().sum()),
                                           invalid_examples=json.dumps(unparsed.dropna().head(5).tolist(), default=str),
                                           not_applicable_no_time_field=int(not bool(time)),
                                           raw_time_formats=json.dumps(formats), time_precision=precision, timezone=zone,
                                           rmi_fid_timestamp_mismatches=fid_mismatch,
                                           backward_steps=int((times[valid].diff().dt.total_seconds() < 0).sum()),
                                           duplicate_timestamp_excess=int(valid.sum()) - len(ts),
                                           exact_duplicate_rows_excess=int(df.duplicated().sum()),
                                           calendar_years="|".join(map(str, sorted(set(ts.year)))),
                                           first=tstr(ts.min()), last=tstr(ts.max())))
                    if path.stem.startswith("WP_"):
                        wp[path.stem + path.suffix] = wp_content(df, times)
                    if path.stem in {"2020a_fruit", "2020b_fruit", "Fruit_2020", "LA_2021", "Leaves_2021", "Central_2023", "Central_2024"}:
                        reference_tables[path.stem] = df.assign(parsed_time=times)
                groups = {"": df.index}
                if path.stem == "RMI_2022":
                    groups = {str(code): ids for code, ids in df.groupby("code").groups.items()}
                for sensor, ids in groups.items():
                    valid_times = times.loc[ids].dropna()
                    year_groups = {int(y): ix.index for y, ix in valid_times.groupby(valid_times.dt.year)}
                    if times.loc[ids].isna().any():
                        year_groups["unknown"] = times.loc[ids][times.loc[ids].isna()].index
                    for actual, idx in year_groups.items():
                        st = datetime_set(times.loc[idx])
                        if not metadata:
                            coverage.append(dict(details, sensor=sensor, calendar_year=actual, records=len(idx),
                                                 n_times=len(st), first=tstr(st.min()), last=tstr(st.max()),
                                                 n_dates=len(st.normalize().unique()), precision=precision))
                norm = df.apply(lambda col: col.map(normalize))
                available = norm.notna()
                for j, f in enumerate(df):
                    raw = df[f]
                    values = norm[f]
                    blanks = raw.isna() | raw.map(lambda v: isinstance(v, str) and not v.strip())
                    absent = values.isna()
                    present = values[~absent]
                    numerics = pd.to_numeric(present, errors="coerce") if f != time else pd.Series([], dtype=float)
                    invalid_numeric = numerics.isin([np.inf, -np.inf])
                    available.loc[invalid_numeric[invalid_numeric].index, f] = False
                    key, unit, meaning, origin, note = field_info(path, tab, f, time)
                    if metadata:
                        unit, meaning, origin, note = "依条目", f, "说明/传感器布置元数据", "元数据行不计作观测"
                    if f == time:
                        dtype = ("mixed valid and invalid time" if valid.any() and (~valid & ~absent).any() else
                                 "unresolved time" if not valid.any() else precision)
                    elif present.empty:
                        dtype = "empty"
                    elif numerics.notna().all():
                        dtype = "numeric"
                    elif numerics.notna().any():
                        dtype = "mixed numeric/text"
                    else:
                        dtype = "text"
                    if path.stem.startswith("Plant_") and present.astype(str).str.contains(",").any():
                        note = "单元格内含逗号分隔多值；个体映射未给出"
                        dtype = "list in cell / mixed"
                    column = openpyxl.utils.get_column_letter(columns[j] + 1)
                    fields.append(dict(details, column=column, original_field=f, standard_field=key, data_type=dtype,
                                       original_unit=unit, meaning=meaning, provenance=origin, n_records=len(raw),
                                       n_missing=int(absent.sum()), missing_fraction=float(absent.mean()), notes=note,
                                       source_formula_cells=formulas.get(column, 0)))
                    for sensor, ids in groups.items():
                        core = dict(details, field=f, sensor=sensor)
                        if metadata:
                            year_groups = {"not applicable": ids}
                        else:
                            year_groups = {"all": ids}
                            vt = times.loc[ids].dropna()
                            year_groups.update({int(y): ix.index for y, ix in vt.groupby(vt.dt.year)})
                            if time and times.loc[ids].isna().any():
                                year_groups["unknown"] = times.loc[ids][times.loc[ids].isna()].index
                        for actual, idx in year_groups.items():
                            v = values.loc[idx]
                            a = v.isna()
                            local = dict(core, calendar_year=actual)
                            runs = list(true_runs(a.to_numpy()))
                            for start, stop in runs:
                                if actual not in {"all", "not applicable"} and len(year_groups) == 2:
                                    continue
                                missing_runs.append(dict(local, first_record_index=int(idx[start])+1,
                                                         last_record_index=int(idx[stop-1])+1, n=stop-start,
                                                         first=tstr(times.loc[idx[start]]), last=tstr(times.loc[idx[stop-1]])))
                            n = pd.to_numeric(v, errors="coerce") if f != time else pd.Series(np.nan, index=v.index)
                            infinite = n.isin([np.inf, -np.inf])
                            n = n.mask(infinite)
                            miss_count = int(a.sum())
                            numeric_typed = bool(n.notna().any())
                            row_stat = dict(local, n_records=len(idx), n_missing=miss_count,
                                            missing_fraction=miss_count/len(idx) if len(idx) else None,
                                            blanks=int(blanks.loc[idx].sum()),
                                            encoded_missing=miss_count-int(blanks.loc[idx].sum()),
                                            encoded_values=json.dumps(raw.loc[idx][a & ~blanks.loc[idx]].value_counts().to_dict()),
                                            longest_missing_run_rows=max((b-a for a,b in runs), default=0),
                                            unique_nonmissing=int(v.nunique()), zero_values=int((n == 0).sum()),
                                            nonfinite_numeric=int(infinite.sum()),
                                            rh_outside_0_100=int(((n < 0) | (n > 100)).sum()) if unit == "%" and "humidity" in key else None,
                                            minimum_numeric=float(n.min()) if numeric_typed else None,
                                            maximum_numeric=float(n.max()) if numeric_typed else None)
                            if numeric_typed and origin not in {"采样设计元数据", "时间索引", "记录元数据"} and not metadata:
                                same = (n.notna() & n.eq(n.shift())).to_numpy()
                                stable = list(true_runs(same))
                                row_stat["longest_flat_run_rows"] = max((b-a+1 for a,b in stable), default=int(n.notna().any()))
                                if actual == "all":
                                    for start, stop in stable:
                                        if stop - start >= 19:
                                            flat_runs.append(dict(local, first_record_index=int(idx[start-1])+1,
                                                                  last_record_index=int(idx[stop-1])+1, n=stop-start+1,
                                                                  value=n.iloc[start], first=tstr(times.loc[idx[start-1]]),
                                                                  last=tstr(times.loc[idx[stop-1]])))
                            misses.append(row_stat)
                        if f != time and not metadata and time:
                            usable = times.loc[ids][available[f].loc[ids]]
                            for actual, sample in [("all", usable), *usable.dropna().groupby(usable.dropna().dt.year)]:
                                index = datetime_set(sample)
                                metrics = sampling_stats(index)
                                sampling.append(dict(core, calendar_year=actual, time_precision=precision,
                                                     n_nonmissing_field_rows=len(sample), n_without_usable_time=int(sample.isna().sum()), **metrics))
                                if actual != "all":
                                    continue
                                delta = intervals(index)
                                for i in np.flatnonzero(delta > metrics.get("gap_threshold_seconds", np.inf)):
                                    gaps.append(dict(core, first=tstr(index[i]), last=tstr(index[i+1]), seconds=delta[i]))
                if not metadata:
                    mask = norm.isna()
                    for i, f in enumerate(mask):
                        for g in list(mask)[i+1:]:
                            a, b = mask[f], mask[g]
                            both, either = int((a & b).sum()), int((a | b).sum())
                            missing_pairs.append(dict(details, field_a=f, field_b=g, both_missing=both,
                                                      either_missing=either, jaccard=both/either if either else None))
                    if path.parts[0] == "Environment" or path.stem == "Transpiration_2022":
                        for sensor, ids in groups.items():
                            observed_times[(path.as_posix(), tab, sensor)] = datetime_set(times.loc[ids])
                            for f in df:
                                if f not in {time, "FID", "the_geom", "code", "wind_speed_unit"} and not f.startswith("(unnamed"):
                                    idx = datetime_set(times.loc[ids][available[f].loc[ids]])
                                    if len(idx):
                                        streams[(path.as_posix(), tab, sensor, f)] = idx
            files.append(dict(source, archive_member=name, bytes=len(data), sha256=sha256,
                              n_tabs=tab_count, n_data_rows=records, n_metadata_rows=metadata_rows, purpose=file_role(path),
                              official_download=official["download_url"], official_match=True))

    if catalog:
        raise ValueError(f"Official files absent from the ZIP: {sorted(catalog)}")
    diagnostics = {}
    verify_targets(wp, streams, targets, align, diagnostics)
    align_streams(streams, observed_times, align)
    joint = joint_environment(streams, align)
    verify_related_tables(reference_tables, diagnostics)
    for filename in ("9hyv69sm82_v1.zip", "mendeley_snapshot.json", "mendeley_folders.json", CATALOG.name):
        data = (ROOT / "raw" / filename).read_bytes()
        files.append(dict(file=f"raw/{filename}", bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                          purpose="官方 ZIP 容器" if filename.endswith(".zip") else "官方 API 元数据快照"))
    results = {"file_inventory": files, "column_dictionary": fields, "missingness_summary": misses,
               "timestamp_quality": ts_quality, "sampling_frequency": sampling, "site_year_coverage": coverage,
               "missing_intervals": missing_runs, "missingness_pairs": missing_pairs, "gap_intervals": gaps,
               "constant_intervals": flat_runs, "target_label_audit": targets, "variable_alignment": align}
    for name, rows in results.items():
        write_csv(name, rows)
    summary = dict(archive_members=archive_count, observation_files=sum(r.get("n_data_rows", 0) > 0 for r in files),
                   data_rows=sum(r.get("n_data_rows", 0) for r in files),
                   metadata_rows=sum(r.get("n_metadata_rows", 0) for r in files),
                   data_columns=sum(r["role"] != "metadata" for r in fields),
                   named_nonempty_data_columns=sum(r["role"] != "metadata" and r["n_missing"] < r["n_records"]
                                                   and not r["original_field"].startswith("(unnamed") for r in fields),
                   unique_named_nonempty_raw_headers=len({r["original_field"] for r in fields if r["role"] != "metadata"
                                                        and r["n_missing"] < r["n_records"] and not r["original_field"].startswith("(unnamed")}),
                   unparsed_nonblank_times=sum(r["invalid_nonblank"] for r in ts_quality),
                   **diagnostics)
    (ROOT / "audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    from figures import plot_audit
    plot_audit(results, wp, joint)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for location in sorted({row["site"] for row in files if "site" in row}):
        years = sorted({row["campaign_year"] for row in files if row.get("site") == location})
        print(f"{location}: {', '.join(map(str, years))}")
    for row in targets:
        if row["scope"] == "file" and row["included"]:
            print(f'{row["site"]} {row["filename_year"]}: {row["admitted_values"]} MPa values, '
                  f'{row["distinct_times"]} times on {row["distinct_days"]} day(s)')
    print("主要问题 / Issues: copied 2024 WP, three labelled days, truncated time in 13 CSVs, asynchronous channels.")
    print("RQ1/RQ2 CONDITIONAL GO (descriptive pilot only); RQ3 CONDITIONAL GO (exploratory only); RQ4 NO-GO.")
    print("Current scope: NO-GO. Narrow data-gated follow-up: CONDITIONAL GO.")
    for file in sorted(ROOT.glob("*.md")):
        print(file)


def verify_related_tables(tables, diagnostics):
    fruit_cols = ["parsed_time", "Diameter", "Fresh weight", "Dry weight"]
    csv_fruit = pd.concat([tables["2020a_fruit"], tables["2020b_fruit"]])[fruit_cols].reset_index(drop=True)
    xlsx_fruit = tables["Fruit_2020"][fruit_cols].reset_index(drop=True)
    for f in fruit_cols[1:]:
        csv_fruit[f] = pd.to_numeric(csv_fruit[f], errors="coerce")
        xlsx_fruit[f] = pd.to_numeric(xlsx_fruit[f], errors="coerce")
    same = (csv_fruit == xlsx_fruit) | (csv_fruit.isna() & xlsx_fruit.isna())
    diagnostics["fruit_rows_matching_by_position_date_diameter_weights"] = int(same.all(axis=1).sum())
    leaf_key = ["Days after planting", "Plant", "Leaf", "Fresh weight", "Dry weight", "FW", "DW"]
    leaf_join = tables["LA_2021"][leaf_key].merge(tables["Leaves_2021"][leaf_key].drop_duplicates(), on=leaf_key, how="left", indicator=True)
    diagnostics["la_2021_rows_matching_leaves_2021"] = int((leaf_join["_merge"] == "both").sum())
    left, right = (tables[n].set_index("parsed_time")[["T", "RH", "G"]] for n in ("Central_2023", "Central_2024"))
    common = left.index.intersection(right.index)
    a, b = left.loc[common], right.loc[common]
    diagnostics["central_2023_2024_shared_times"] = len(common)
    diagnostics["central_2023_2024_shared_times_with_all_fields_matching_including_blanks"] = int(
        ((a == b) | (a.isna() & b.isna())).all(axis=1).sum()
    )


def align_streams(streams, observed_times, output):
    items = list(streams.items())
    for i, (a, left) in enumerate(items):
        for b, right in items[i+1:]:
            if a[0].split("/")[1] != b[0].split("/")[1]:
                continue
            if any("/RMI_" in k[0] for k in (a,b)) and a[:3] != b[:3]:
                continue
            start, end = max(left.min(), right.min()), min(left.max(), right.max())
            shared = left.intersection(right)
            query = left[(left >= start) & (left <= end)]
            delta = np.minimum(before(right, query), after(right, query))
            recorded = observed_times[a[:3]].intersection(observed_times[b[:3]])
            missing_a, missing_b = ~recorded.isin(left), ~recorded.isin(right)
            either, both = int(sum(missing_a | missing_b)), int(sum(missing_a & missing_b))
            output.append(dict(kind="environment_or_scale_pair", source_a=" | ".join(a), source_b=" | ".join(b),
                               query_times=len(query), shared_exact=len(shared), nearest_le_15m=int(sum(delta <= 900)),
                               median_nearest_s=float(np.median(delta)) if len(query) else None,
                               first=tstr(start) if start <= end else "", last=tstr(end) if start <= end else "",
                               shared_recorded_times=len(recorded), both_missing_at_shared_times=both,
                               either_missing_at_shared_times=either,
                               missing_jaccard_at_shared_times=both/either if either else None,
                               site=site(int(a[0].split("/")[1])), campaign_year=int(a[0].split("/")[1]),
                               comment="Naive clock comparison only; no timezone or clock-drift correction"))


def joint_environment(streams, output):
    definitions = {
        2019: ("Central_2019", "Light_2019", "Data", "G", 300, 600),
        2020: ("Central_2020", "S3_2020", "Data", "base_Light", 300, 300),
        2021: ("Central_2021", "Light_2021", "GreenhouseClimateHourly", "TotalIradiation_top", 3600, 900),
        2022: ("S_2022", "Light_2022", "Top", "G", 600, 600),
        2023: ("Central_2023", "Central_2023", "Central_2023csv", "G", 300, 300),
        2024: ("Central_2024", "Central_2024", "Central_2024", "G", 300, 300),
    }
    accepted_times = {}
    for year, (th, light, tab, field, step, step_g) in definitions.items():
        t, rh = [next(idx for k, idx in streams.items()
                      if Path(k[0]).stem == th and k[1] == tab and k[3] == f) for f in ("T", "RH")]
        g = next(idx for k, idx in streams.items() if Path(k[0]).stem == light and k[3] == field)
        first, last = max(t.min(), rh.min(), g.min()), min(t.max(), rh.max(), g.max())
        # Hourly diagnostic grid, 60-minute lookback; raw observations are not resampled.
        grid = pd.date_range(first.ceil("h"), last.floor("h"), freq="h").as_unit("ns")
        accepted = grid[stable_windows(t, grid, step, 60, 0) &
                        stable_windows(rh, grid, step, 60, 0) &
                        stable_windows(g, grid, step_g, 60, 0)]
        accepted_times[year] = (grid, accepted)
        output.append(dict(kind="joint_environment_hourly_probe", source_a=f"{th} | {tab} | T/RH",
                           source_b=f"{light} | {field}", campaign_year=year, site=site(year),
                           first=tstr(first), last=tstr(last), query_times=len(grid),
                           shared_exact=len(t.intersection(rh).intersection(g)), causal_60m_window=len(accepted),
                           comment="Hourly probe on overlap; 2020 Light units unknown; 2022 top layer only; clock basis unconfirmed"))
    return accepted_times


def verify_targets(wp, streams, output, align, diagnostics):
    if not wp["WP_2022.csv"].equals(wp["WP_2022.xlsx"]):
        raise ValueError("2022 copies differ; reassess canonicalization")
    if not wp["WP_2024.xlsx"].equals(wp["WP_2023.xlsx"]):
        raise ValueError("2024 differs from 2023; reassess canonicalization")
    diagnostics["identical_target_table_pairs"] = [["WP_2022.csv", "WP_2022.xlsx"], ["WP_2024.xlsx", "WP_2023.xlsx"]]
    diagnostics["target_values"] = diagnostics["target_rows"] = diagnostics["target_times"] = diagnostics["target_days"] = 0
    channels = [f"WP{i}" for i in range(1, 5)]
    for year in EXPECTED_YEARS:
        name = f"WP_{year}.xlsx"
        chosen = wp.get(name)
        variants = [("", None)] if chosen is None else [(name, chosen)]
        if year == 2022:
            variants.append(("WP_2022.csv", wp["WP_2022.csv"]))
        for filename, df in variants:
            canonical = year in (2022, 2023) and filename.endswith(".xlsx")
            times = datetime_set(df.Time) if df is not None else pd.DatetimeIndex([])
            record = dict(site=site(year), filename_year=year, file=filename, scope="file",
                          included=canonical, calendar_years="|".join(map(str, sorted(set(times.year)))),
                          rows=len(df) if df is not None else 0, distinct_times=len(times),
                          distinct_days=len(times.normalize().unique()), first=tstr(times.min()), last=tstr(times.max()),
                          raw_finite_values=0, admitted_values=0)
            if df is not None:
                numbers = df[channels].where(np.isfinite(df[channels]))
                total = int(numbers.count().sum())
                record.update(raw_finite_values=total, admitted_values=total if canonical else 0,
                              time_measurement_key_duplicate_excess=int(df.duplicated(["Time","Measurement"]).sum()),
                              minimum_mpa=numbers.min().min(), maximum_mpa=numbers.max().max(),
                              **{f: int(numbers[f].count()) for f in channels},
                              note="canonical worksheet" if canonical else
                              "same values and times as 2022 XLSX" if year == 2022 else "same values and times as 2023 XLSX")
            else:
                record["note"] = "no documented stem WP source"
            output.append(record)
            if not canonical:
                continue
            diagnostics["target_values"] += record["admitted_values"]
            diagnostics["target_rows"] += record["rows"]
            diagnostics["target_times"] += record["distinct_times"]
            diagnostics["target_days"] += record["distinct_days"]
            for day, local in df.groupby(df.Time.dt.normalize()):
                dt = datetime_set(local.Time)
                pairs = local[channels].count()
                output.append(dict(site=site(year), filename_year=year, file=filename, scope="sampling_day",
                                   included=True, calendar_years=str(year), date=day.date(), rows=len(local),
                                   distinct_times=len(dt), distinct_days=1, first=tstr(dt.min()), last=tstr(dt.max()),
                                   raw_finite_values=int(pairs.sum()), admitted_values=int(pairs.sum()),
                                   minimum_mpa=local[channels].min().min(), maximum_mpa=local[channels].max().max(),
                                   **pairs.to_dict(), within_day_interval_seconds=json.dumps(intervals(dt).tolist())))
            names = (["S_2022.xlsx", "Light_2022.xlsx"] if year == 2022 else ["Central_2023.xlsx"])
            env = {k: idx for k, idx in streams.items() if any(k[0].endswith(x) for x in names)}
            common = {}
            for lead in (0, 30, 60):
                common[lead] = np.ones(len(times), dtype=bool)
                for k, idx in env.items():
                    step = 600 if year == 2022 else 300
                    usable = stable_windows(idx, times, step, minutes=60, lead=lead)
                    common[lead] &= usable
                    if lead == 0:
                        back = before(idx, times)
                        near = np.minimum(back, after(idx, times))
                        align.append(dict(kind="target_to_environment", source_a=filename, source_b=" | ".join(k),
                                          site=site(year), campaign_year=year, query_times=len(times), shared_exact=int(sum(back == 0)),
                                          nearest_le_15m=int(sum(near <= 900)), median_nearest_s=np.median(near),
                                          causal_le_15m=int(sum(back <= 900)), causal_60m_window=int(usable.sum()),
                                          first=tstr(max(idx.min(), times.min())), last=tstr(min(idx.max(), times.max())),
                                          comment="Naive alignment; 60 min window, endpoint tolerance one cadence, max gap two cadences"))
                matched = times[common[lead]]
                matched_df = df[df.Time.isin(matched)]
                output.append(dict(site=site(year), filename_year=year, file=filename, scope="joint_60m_window",
                                   lead_minutes=lead, included=True, distinct_times=len(matched),
                                   distinct_days=len(matched.normalize().unique()), rows=len(matched_df),
                                   raw_finite_values=int(matched_df[channels].count().sum()),
                                   first=tstr(matched.min()), last=tstr(matched.max()),
                                   note="all listed indoor channels; no forward matching; clocks assumed comparable for diagnostic only"))
            for t, ok0, ok30, ok60 in zip(times, common[0], common[30], common[60], strict=True):
                output.append(dict(site=site(year), filename_year=year, file=filename, scope="sampling_time",
                                   included=True, first=tstr(t), rows=int((df.Time == t).sum()),
                                   admitted_values=int(df.loc[df.Time == t, channels].count().sum()),
                                   joint_60m_lead_0=ok0, joint_60m_lead_30=ok30, joint_60m_lead_60=ok60))
def probe():
    with ZipFile(ARCHIVE) as z:
        for name in sorted(z.namelist()):
            path = Path(*Path(name).parts[1:])
            if path.stem == "Info":
                continue
            for tab, rows, formulas in sheets(path, z.read(name)):
                if tab == "Info":
                    continue
                df, _ = frame(rows)
                time = next((x for x in ("Time", "Date", "timestamp") if x in df), None)
                print(path.as_posix(), tab, len(df), list(df), "FORMULAS", formulas)
                if time:
                    t, fmt = parse_time(df[time])
                    missing = t.isna()
                    print(" ", t.min(), t.max(), fmt, "bad", missing.sum(), df.loc[missing, time].value_counts(dropna=False).head(5).to_dict())
                if path.stem.startswith("WP_"):
                    print(df.to_string(index=False))


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        audit()
