# Horti-M3-Tomato 原始数据审查

2026-11-08 从本地复用 Stage 2 的 57 个定向获取成员，合计 6,123,964 字节。逐一按官方 ZIP 目录的大小、CRC32 核验，并生成独立 SHA-256。ZIP 为 28,284,894,698 字节、8,291 个文件成员。57 个选定成员均在，覆盖 34 份传感器 CSV／XLSX、19 份植株 CSV 和四份说明及补充文件。读取 517 张传感表、19 张植株表、6 张 Word 数据表。README 单列为元数据。原文件保持原字节。 [Zenodo 版本](https://doi.org/10.5281/zenodo.17217565) · [论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC13168689/) · [来源清单](source_manifest.csv)

## 采集与筛选

| 种植季 | 传感原表行（含重复导出） | 排除逐字节重复文件后传感行 | 处理 2／5 每通道活动期有效 WC 槽 |
|---|---:|---:|---:|
| 2023 夏 | 44,021 | 26,479 | 2,877 / 2,976 |
| 2024 秋 | 13,428 | 13,428 | 2,537 / 2,592 |
| 2025 春 | 14,325 | 14,325 | 2,285 / 2,688 |

- 2023 年 5、6 月有七对逐字节重复文件，其中六对传感器 CSV、一对植株 CSV。处理 2 的两份 CSV 大小和 CRC 不同，但同槽且同时间的 2,925 条记录六个信号全部一致（容差 0.0001），同时间同值行只计一次。7 月 Excel 与早期 CSV 另有同半小时槽、不同时间精度的重叠。处理 2 和处理 5 各 48 槽六信号一致，按冻结协议保守剔除；其余处理的重叠也逐项记录。没有依据文件夹月份复制生成训练样本。 [逐文件比对](statistics/overlapping_source_members.csv) · [不唯一槽](statistics/ambiguous_slots.csv)
- 排除逐字节重复文件后，传感器表内 2023、2024、2025 的重复时间戳额外行数分别为 111、684、3,448。2025 有 32 个同表内的冲突时间戳和 1 次逆序。2024、2025 按工作簿存放顺序串接时有 125、105 次时间逆跳，其中绝大部分是日期工作表排列导致。所有传感行的时标均可解析；日期命名工作表中的时间未越出标注日期。 [逐表统计](statistics/sheet_audit.csv) · [工作簿顺序诊断](statistics/file_clock_audit.csv)
- 传感器通道未见空单元格，但实际可用性受整槽不在、同槽冲突及部分同组全零编码影响。2024 和 2025 的气温、相对湿度、CO₂ 同为 0 的行应视为观测不可用候选；2025 另有 WC 与土温同为 0 的候选。保留原表零值并列出数量。预测输入仅按协议拒用联合零，光照零值本身可代表夜间。数据未公开故障或通信日志。 [逐字段缺失、零值和范围](statistics/field_audit.csv) · [信号可用率](statistics/signal_availability.csv) · [信号缺口](statistics/signal_gap_runs.csv)
- 人工表为植株级观测，保留原始日期和编号。2023 的合表有 `Pid`、`日期`、`高度`、`叶面积` 以及 NDVI 等冠层仪指标。2024、2025 的单日文件有 `编号`、`株高`、`茎粗` 等。2023 合表附带的 `土壤水分` 等环境列只有日级对应，来源聚合和测量时刻未证实。报告中明确区分原始半小时传感值、日级附加值与人工或手持仪观测，后三类未进入预测。原资料声称 2023 前两周部分冠层指标缺测，但 CSV 含早期有限值，不能据此补齐实际采集史。 [字段审查](statistics/field_audit.csv) · [第二阶段植株日统计](../dataset_scouting_round2/statistics/horti_measurement_days.csv)

## 未下载范围

57 个已选成员缺失数为 **0**。ZIP 中其余 8,234 个成员未获取，包括 7,856 个图像文件、372 个处理后成员，以及六个补充文件：

- `2024 Tomato Growth Record Table.xlsx`
- `2024 Tomato Production Data.xlsx`
- `2025 Tomato Farming Records.xlsx`
- `2025 Tomato Growth Record Table.xlsx`
- `2025 Tomato Production Data.xlsx`
- `Plant Orientation Diagram.png`

完整 28 GB ZIP 未下载。所有选定传感原表和植株原表都可在本地现有目录复核。 [未获取类别汇总](statistics/unacquired_archive_summary.csv) · [完整成员索引](../dataset_scouting_round2/raw/catalog/horti_zip_members.csv) · [Stage 2 审查](../dataset_scouting_round2/TOP3_DATASET_AUDIT.md)
