# AgriXAI-Reliability · 第二阶段数据调查

**CONDITIONAL GO。** 建议从农业根区水分短期预测切入。以 Parma Evolving Testbed 做跨季主实验，以 WUR Tomato Challenge v2 做独立温室复现。Horti-M3 提供温室跨季备选，电生理适合单批留株复现。 [最终建议](FINAL_RESEARCH_RECOMMENDATION.md)

## 报告

1. [全球检索范围与计数](GLOBAL_DATASET_SEARCH.md)
2. [候选比较与评分](DATASET_COMPARISON.md)
3. [前三名原件审查及电生理深查](TOP3_DATASET_AUDIT.md)
4. [方法级文献排重](RELATED_WORK_UPDATE.md)
5. [方向比较、实验边界与两年范围](RESEARCH_DIRECTION_COMPARISON.md)
6. [最终建议与十二项回答](FINAL_RESEARCH_RECOMMENDATION.md)

## 结构化交付

[数据集总表](dataset_inventory.csv) · [元数据](dataset_metadata.csv) · [目标](target_variable_comparison.csv) · [采样](sampling_frequency_comparison.csv) · [标签质量](label_quality_comparison.csv) · [独立单位](independent_units_comparison.csv) · [数据评分](dataset_feasibility_scores.csv) · [方向评分](research_direction_scores.csv) · [文献对照](literature_comparison.csv)

[原件与官方凭证](raw/) · [原包校验](statistics/source_archive_checksums.csv) · [逐件哈希](statistics/raw_file_inventory.csv) · [字段字典](statistics/column_dictionary.csv) · [表级统计](statistics/table_profiles.csv) · [流级间隔](statistics/sampling_by_stream.csv) · [标签分布](statistics/raw_label_distribution.csv)

## 复算

Python 3.12 环境。已下载的 ZIP、7z、XLSX 和 CSV 保持原值；对 Zenodo 大包的所选成员保存原始解压字节，原版 ZIP 成员索引和选择清单保存在 [raw/catalog](raw/catalog/)。电生理路径的冒号本地以 `%3A` 转义，索引保留原名。WUR 的 7z 已在 `raw/wur/extracted` 展开并保留原包。运行以下命令只更新 `statistics` 的原件计算结果。九份候选与文献 CSV 含人工证据分级及评分。

```powershell
python -m pip install -r scripts/requirements.txt
python scripts/audit.py
```

若需要从官方远程归档重取指定原件，读取 `*_selection.txt` 后用 `scripts/remote_zip.py <horti|electro|lettuce> --pick <selection.txt>`。脚本要求符合预期的 HTTP 206、Content-Range、大小与 CRC。Horti 图像和电生理其他窗口尺度无需重取。首轮工作区的 Mendeley 原包仅在比利时重叠比对中被读入。所有范围裁剪、分组、缺失和窗口只为审查统计，未修改源文件。

22 项候选、7 项直接生理目标、13 项有连续记录段、8 项具各自任务的分组测试条件。各数字的定义、重叠及原件/文献等级见 [检索报告](GLOBAL_DATASET_SEARCH.md)。
