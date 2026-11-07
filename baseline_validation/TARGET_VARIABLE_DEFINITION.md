# 第三阶段目标变量定义

| 项目 | Task A · Parma | Task B · WUR |
|---|---|---|
| 原始表与字段 | 每年的 `soilYYYY.csv` 中 `humidity`，主键为年、`line`、`generated_by`、`ts_generation` | 各隔间 `GrodanSens.csv` 的 `WC_slab1`、`WC_slab2`，主键为隔间、岩棉通道、`%time` |
| 对象和单位 | 番茄田矿质土壤根区、单探头敏感体积内的含水量。按 EM500-SMTC 的土壤水分百分数原样使用，误差以传感器输出百分点计 | 单块岩棉板根区的体积含水量（含水体积／岩棉总体积），GroSens `% v/v`，误差以岩棉体积百分点计 |
| 类型 | LoRaWAN 多参数插入式土壤探头 Milesight EM500-SMTC，同时测温度与电导率 | 插入岩棉的 Grodan GroSens MultiSensor；同一块岩棉亦有温度和电导率读数 |
| 时间 | `ts_generation` 是设备数据生成记录的 Unix 毫秒，按 UTC 解析。发布频率为 10 分钟。没有单独的到达时间或测量到入库延迟 | `%time` 是气候计算机导出的 Excel 日期序列，按 1899-12-30 起算，设备根区原序列 3 分钟一次，发布者处理为 5 分钟与温室气候同步。说明未给出重采样算子、槽位代表时刻、时区或上报时延 |
| 探头关系 | 2023 年 3 条灌溉线各 1 支。发布 README 和 2023 原数据论文标 20 cm，后续两季研究将第一季写为 15 cm，按文件记载并保留冲突。2024、2025 年各 5 条线，每线 15 和 30 cm 两支，公开 CSV 没有逐设备深度映射。两探头共用同一线环境与灌溉，原始设备标识可跨年复用 | 6 隔间，每隔间 2 岩棉板测点，隔间内通道共用气候管理与相当多的缺失时刻。使用各自未来读数，逐通道保留，两通道属于同一隔间评估单元 |
| 校准 | 厂商说明土壤水分百分数解码和设备偏移校准。公开数据未提供本地土质校准曲线、启用的偏移或校验记录 | 厂商手册定义量纲和基质类型设置。挑战赛公开文件未提供逐设备校验、原始介电测量或当时 Smartbox 配置 |

## 物理判读与范围

- **Soil Volumetric Water Content** 是水体积占土壤总体积的比值。Parma 数据说明对土壤 `humidity` 写作 `%RH`，同一个字段名在 `environmental` 表确实表示空气相对湿度。土壤表结合硬件与载荷解码可以确认测量对象、导出尺度和 0–100 的范围。本阶段预测的是厂商土壤含水率读数，未独立标定为本站点土壤的绝对体积含水量。绝对土壤 VWC 与同埋深泛化超出该任务定义。 [三季数据和归档 README](https://data.mendeley.com/datasets/h8sfcf9487/1) · [2023 原始数据论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC12002878/) · [两季实验论文](https://doi.org/10.1109/TAFE.2025.3625603) · [Milesight 手册的载荷解码与校准](https://resource.milesight.com/milesight/iot/document/em500-smtc-user-guide-en.pdf)
- **Soil Water Potential** 是水的能量状态。Parma 2024 的 `water_potential2024.csv` 由 Decentlab DL-TRS21 测土壤基质势，单位 kPa。由基质势推算 VWC 需要匹配土壤的水分特征曲线。该表独立保留，退出本阶段预测。 [Decentlab DL-TRS21](https://www.decentlab.com/products/soil-water-potential-and-temperature-sensor-for-lorawan)
- **Substrate Water Content** 泛指栽培基质含水量。WUR 的 **Rockwool Water Content** 是具体岩棉体系的体积分数，由归档 `ReadMe.pdf`、试验供应商和设备手册共同确认。厂家手册还列出岩棉类型与尺寸设置，公开标签是由实测 GroSens 序列处理得到的 5 分钟发布值。归档说明第 17 页明确标注 Processed。与矿质土壤的孔隙、储水特征及探头校准不同，两个域分别拟合与评估。 [WUR 发布页](https://research.wur.nl/en/datasets/autonomous-greenhouse-challenge-second-edition-2019/) · [挑战赛论文](https://doi.org/10.3390/s20226430) · [Grodan 挑战赛部署说明](https://www.grodan.com/global/knowledge/blogs/autonomous-greenhouses-international-challenge/) · [GroSens MultiSensor 手册，第 7 和 9 节](https://www.grodan.nl/syssiteassets/downloads/downloads-en/installation-guides-en/grodan-grosens-installation-manual-multisensor-a4-2020-en.pdf)

两个定义均足够支撑公开传感器读数的预测。Horti-M3-Tomato 沿用第二阶段的备用定位。
