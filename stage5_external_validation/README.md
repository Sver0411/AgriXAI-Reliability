# AgriXAI-Reliability · Stage 5 External Validation

**NO-GO。** Horti-M3 的处理 2 与处理 5 支持基质体积含水率的三季留出基线，但学习模型没有通过对 Persistence 的预设门槛。外部归因实验保留为方案。 [阶段决策](STAGE5_READINESS_DECISION.md)

| 入口 | 内容 |
|---|---|
| [Stage 4 SHA-256 清单](../stage4_temporal_attribution/SHA256SUMS.txt) | 路径、字节数和哈希，含全部既有正式交付 |
| [原始数据审查](HORTI_DATASET_AUDIT.md) · [来源清单](source_manifest.csv) | 57 个选定成员、CRC 与 SHA-256、重复导出、未获取范围 |
| [传感器映射](SENSOR_TARGET_MAPPING.md) · [时间审查](TEMPORAL_ALIGNMENT_AUDIT.md) | 同名通道辨析、实际半小时采样、缺槽和时间对齐 |
| [固定划分](SPLIT_PROTOCOL.md) · [基线报告](BASELINE_EXPERIMENT_REPORT.md) | 30／60／120 分钟，逐季和逐通道，配对周块区间 |
| [外部 XAI 设计](XAI_EXPERIMENT_DESIGN.md) | 后续预测门槛通过时采用的对照 |

## 复算

使用 Python 3.12，在项目根目录运行。传感器 CSV、Excel 日表及附录沿用 Stage 2 的解析器，仅在 Stage 5 目录写审查和预测结果。需要原有 `dataset_scouting_round2/raw/horti/zip_members/` 和 Stage 2 的选件目录。若是没有本地原件的新检出，复用原有的 [远程 ZIP 脚本](../dataset_scouting_round2/scripts/remote_zip.py) 读取官方 ZIP 的选定范围。该下载脚本会重建 Stage 2 的成员索引。已有原件的工作区直接运行审查。

```powershell
python -m pip install -r stage5_external_validation/scripts/requirements.txt
python -B stage4_temporal_attribution/scripts/hash_manifest.py --check
python -B stage5_external_validation/scripts/audit_horti.py
python -B stage5_external_validation/scripts/run_baselines.py
```

在缺少原件的新检出中，运行以下命令取得指定 57 个成员。

```powershell
python -B dataset_scouting_round2/scripts/remote_zip.py horti --pick dataset_scouting_round2/raw/catalog/horti_selection.txt
```

[逐表审查](statistics/sheet_audit.csv) · [逐字段审查](statistics/field_audit.csv) · [逐信号缺槽](statistics/signal_gap_runs.csv) · [入窗清单](statistics/window_eligibility.csv) · [基线指标](statistics/baseline_metrics.csv) · [留出预测](statistics/heldout_predictions.csv)

完整复跑两次后，18 个生成文件的 SHA-256 逐一一致。
