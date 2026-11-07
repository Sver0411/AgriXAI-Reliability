# 温室番茄水势公开数据审查

**当前版本与原题范围的结论为 NO-GO。** 56 个原始文件中有 45 个观察文件，合计 1,695,315 源观察行。两地水势标签去除副本后仅 3 个采样日、22 个时间点及 212 个分层测量值。可以进入描述性配对和解释评价流程预研。 [最终决定](FINAL_DECISION.md)

## 报告

1. [完整数据审查](DATA_AUDIT_REPORT.md)
2. [研究可行性](RESEARCH_FEASIBILITY.md)
3. [相关研究排重](RELATED_WORK.md)
4. [条件性实验方案](EXPERIMENT_PLAN.md)
5. [最终决定与十项回答](FINAL_DECISION.md)

## 结构化证据

| 内容 | 文件 |
|---|---|
| 官方源文件逐件字节数、SHA-256、下载链接 | [file_inventory.csv](file_inventory.csv) |
| 所有表及字段、来源、单位和缺失 | [column_dictionary.csv](column_dictionary.csv) |
| 正采样间隔、众数、分位数、取整分布 | [sampling_frequency.csv](sampling_frequency.csv) · [gap_intervals.csv](gap_intervals.csv) |
| 缺失与恒定检查 | [missingness_summary.csv](missingness_summary.csv) · [missing_intervals.csv](missing_intervals.csv) · [missingness_pairs.csv](missingness_pairs.csv) · [constant_intervals.csv](constant_intervals.csv) |
| 逐源地点和活动年／日历年覆盖 | [site_year_coverage.csv](site_year_coverage.csv) |
| 压力室标签、测量日、时间点及候选窗口 | [target_label_audit.csv](target_label_audit.csv) |
| 时间质量与对齐 | [timestamp_quality.csv](timestamp_quality.csv) · [variable_alignment.csv](variable_alignment.csv) |
| 研究问题、文献对照 | [research_question_feasibility.csv](research_question_feasibility.csv) · [literature_comparison.csv](literature_comparison.csv) |
| 关键可复算计数 | [audit_summary.json](audit_summary.json) |

[整体覆盖](figures/01_overall_coverage.png) · [地点与年份](figures/02_site_year_distribution.png) · [采样间隔](figures/03_sampling_intervals.png) · [字段缺失](figures/04_field_missingness.png) · [共同窗口](figures/05_joint_variable_windows.png) · [标签分布及频率](figures/06_water_potential_labels.png) · [标签对齐](figures/07_label_input_windows.png) · [逐字段缺失率图，可放大](figures/08_field_missingness_detail.svg)

## 从原件重算

在本目录运行，Python 3.12 已验证。推荐使用环境内 Python 安装依赖。包内原件直接从官方 ZIP 按字节读取。程序会逐件与保存的 Mendeley 官方目录匹配，并拒绝内容不一致的文件。

```powershell
python -m pip install -r scripts/requirements.txt
python scripts/audit.py
```

`audit.py` 会覆盖本目录中计算生成的统计 CSV、JSON 和图，打印主要计数、结论与报告绝对路径。文献比较和 RQ 判定表由证据审阅写成，属于解释性判断。用 `python scripts/inspect_sources.py` 可预览所有原表。依赖和原件未被脚本改写。保存的 [官方 ZIP](raw/9hyv69sm82_v1.zip) 为未修改下载物；[来源快照](raw/mendeley_snapshot.json)、[目录](raw/mendeley_folders.json) 及 [官方文件清单 API 汇编](raw/mendeley_file_catalog.json) 可用于离线验真。

### 统计口径

- 行数按源表计算，分开数据和说明。CSV 日期、数值和原空白先按源文本读入，不补值。全空行及纯格式化无名列不计入。
- `campaign_year` 指数据发布或采集活动的来源年，`calendar_year` 指解析后的钟面年；分组表也保留 `all` 和必要的 `unknown`。同一文件内不同列是不同字段实例。`Time`、`Date` 的失败解析在时间质量表列出。
- `missing_intervals.csv` 的行号是全空行去掉后的 1 起算数据记录序号。区间来自原始顺序；同表行级缺失、时间断档与跨源共时缺失分别核算。连续恒定区间明细保存至少 20 条相同数值的段，缺失统计另列最长段。
- 采样表的均值、方差、分位数、众数基于排序后的不同可用时间戳的正差，绝对时间不可判时留空。分布 `n_bins` 是秒数到次数的映射。同一时刻多笔称重与压力室值在计量和文件表中仍可见。
- 目标表 `scope=file` 汇总原始文件与纳入的有效值，`sampling_day/time` 给出规范视图，`joint_60m_window` 记录在三种提前量下的可配对样本，不把这些重复窗口累加为新标签。近邻和共窗是钟面时间诊断，室内时区未知。
- 图以原始数据统计生成，小时探针只用于判定窗口是否存在。近邻和范围包络不说明传感同步、插值或序列连续。

原数据由 Salagovic、Verboven、Hertog、Van de Poel、Nicolai 发布，CC BY 4.0。 [Mendeley v1](https://doi.org/10.17632/9hyv69sm82.1) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
