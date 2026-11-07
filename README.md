# AgriXAI-Reliability

农业短期水分预测与时序归因可靠性研究。当前主实验使用 WUR 温室番茄的岩棉体积含水量，比较完整输入、记录形态缺失与等预算随机缺失对冻结 LightGBM 模型归因的影响。

| 阶段 | 入口 |
|---|---|
| 1 · 温室番茄水分数据初审 | [数据审查与科研可行性](greenhouse_tomato_data_audit/README.md) |
| 2 · 全球公开农业时序数据调查 | [候选数据、原件审查与研究方向](dataset_scouting_round2/README.md) |
| 3 · 独立预测基线 | [实验报告](baseline_validation/BASELINE_EXPERIMENT_REPORT.md) · [预测任务决策](baseline_validation/FINAL_STAGE_DECISION.md) |
| 4 · 时序归因稳定性与忠实性 | [交付索引、图表与复算](stage4_temporal_attribution/README.md) · [第四阶段决策](stage4_temporal_attribution/STAGE4_FINAL_DECISION.md) |
| 5 · 外部验证 | [数据审查与复算](stage5_external_validation/README.md) · [第五阶段决策](stage5_external_validation/STAGE5_READINESS_DECISION.md) |

阶段三的 WUR 60 分钟模型在两个留出隔间均优于 Persistence，合并 MAE 降低 14.45%。Parma 跨季预测未达到预定门槛。阶段四在 WUR 观察到小预测变化下的归因排名变动，以及忠实性评价对替换方式的敏感性。第五阶段用 Horti-M3 做跨季验证。学习模型未通过预设预测门槛，当前候选判定为 **NO-GO**，外部归因实验保留为方案。

## 数据与复算

仓库包含分析代码、审查报告、实验结果、图、冻结模型与供复算的抽样数据。第三方原始归档从官方发布方取得。将下载文件放入原协议指定位置后，可使用各阶段 README 中的命令复算。

| 数据 | 官方来源 | 本地相对位置 |
|---|---|---|
| 第一阶段温室番茄数据 | [Mendeley v1](https://doi.org/10.17632/9hyv69sm82.1) | `greenhouse_tomato_data_audit/raw/9hyv69sm82_v1.zip` |
| Parma Evolving Testbed | [Mendeley v1](https://doi.org/10.17632/h8sfcf9487.1) | `dataset_scouting_round2/raw/evolving/original.zip` |
| WUR Autonomous Greenhouse Challenge, Second Edition | [WUR 发布说明](https://research.wur.nl/en/datasets/autonomous-greenhouse-challenge-second-edition-2019/) · [4TU.ResearchData 归档](https://doi.org/10.4121/uuid:88d22c60-21b3-4ea8-90db-20249a5be2a7) | `dataset_scouting_round2/raw/wur/AutonomousGreenhouseChallenge_edition2.7z`，归档内容解压到同级 `extracted/` |
 
第二阶段的[数据总表](dataset_scouting_round2/dataset_metadata.csv)与[归档校验值](dataset_scouting_round2/statistics/source_archive_checksums.csv)记录其余来源。第四阶段可以直接使用仓库内的冻结 Booster、训练背景样本和测试窗运行归因步骤；重新恢复预测模型需要阶段二原件。
