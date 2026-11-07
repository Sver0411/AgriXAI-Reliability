# AgriXAI-Reliability · 第四阶段

研究对象是 WUR 公开五分钟温室岩棉体积含水量，主实验预测提前量为 60 分钟，辅助为 120 分钟。实验沿用阶段三的 LightGBM、划分、标签和特征定义。以传感器、历史区间及其交组为单位，比较完整输入、记录形态遮蔽、等预算随机遮蔽及四种连续遮蔽对照。 [第三阶段结果](../baseline_validation/FINAL_STAGE_DECISION.md) · [第二阶段研究依据](../dataset_scouting_round2/FINAL_RESEARCH_RECOMMENDATION.md)

## 报告

1. [实验设计与固定判据](XAI_METHOD_DESIGN.md)
2. [模型重现与归因基线](ATTRIBUTION_BASELINE_REPORT.md)
3. [传感扰动与归因稳定性](ATTRIBUTION_STABILITY_REPORT.md)
4. [删除、插入与忠实性](FAITHFULNESS_EXPERIMENT_REPORT.md)
5. [配对统计与不确定性](STATISTICAL_ANALYSIS.md)
6. [十项研究判断与第五阶段决定](STAGE4_FINAL_DECISION.md)

## 数据、模型与结果

- [模型版本、哈希及预处理](frozen/model_manifest.json) · [60 分钟模型](frozen/lightgbm_60min.txt) · [120 分钟模型](frozen/lightgbm_120min.txt) · [第三阶段复现](frozen/stage3_reproduction.csv) · [特征顺序](frozen/feature_order.csv)
- [测试与验证样本](frozen/sample_manifest.csv) · [训练背景清单](frozen/training_background_manifest.csv) · [逐窗背景匹配](frozen/matched_donors.csv) · [验证阈值](frozen/validation_thresholds.json) · [输入、背景与遮蔽数组](frozen/windows_and_masks.npz) · [Mask Manifest](mask_manifest.csv)
- [全局特征](attribution_global.csv) · [分传感器](attribution_by_sensor.csv) · [分时间](attribution_by_time.csv) · [传感器 × 时间](attribution_by_sensor_time.csv) · [带符号的逐窗归因数组](local_attributions.npz) · [TreeSHAP 闭合误差](treeshap_additivity.csv)
- [逐窗口逐实现稳定性](attribution_stability.csv) · [逐窗预测—归因关系](prediction_attribution_relationship.csv) · [预测—归因统计](prediction_relationship_summary.csv) · [配对扰动与缺失预算分层](perturbation_comparison.csv)
- [忠实性指标](faithfulness_metrics.csv) · [删插曲线及周块区间](deletion_insertion_curves.csv) · [逐窗忠实性](faithfulness_window_metrics.csv) · [替换质量审查](replacement_diagnostics.csv) · [每个隔间的结果](per_compartment_results.csv)

- [边界调整忠实性](boundary_sensitivity_faithfulness.csv) · [边界调整稳定性](boundary_sensitivity_stability.csv) · [边界调整诊断](boundary_sensitivity_diagnostics.csv) · [边界调整曲线](boundary_sensitivity_curves.csv)

## 图表

| | | |
|---|---|---|
| [01 传感器归因排序](figures/01_sensor_ranking.png) | [02 历史窗口热图](figures/02_temporal_heatmap.png) | [03 扰动模式](figures/03_perturbation_comparison.png) |
| [04 完整与受扰归因](figures/04_complete_perturbed.png) | [05 预测与归因散点](figures/05_prediction_attribution_scatter.png) | [06 删除](figures/06_deletion.png) |
| [07 插入](figures/07_insertion.png) | [08 隔间对比](figures/08_compartment_stability.png) | [09 忠实性指标](figures/09_faithfulness_comparison.png) |

## 复算

从项目根目录使用 Python 3.12。运行脚本只写第四阶段输出。先重建并校验模型，第二步及以后也可以从交付的冻结模型和数组直接开始。五个脚本都支持同一个独立 `--output` 目录，完整复跑时应依序执行。

```powershell
py -3.12 -m venv .venv-stage4
$python = '.\.venv-stage4\Scripts\python.exe'
& $python -m pip install -r stage4_temporal_attribution/scripts/requirements.txt
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
& $python -B stage4_temporal_attribution/scripts/freeze_models.py
& $python -B stage4_temporal_attribution/scripts/run_attribution.py
& $python -B stage4_temporal_attribution/scripts/summarize.py
& $python -B stage4_temporal_attribution/scripts/plot_results.py
& $python -B stage4_temporal_attribution/scripts/boundary_sensitivity.py
```

`mask_little_endian_hex` 存放 52 格按行优先展平后的位串。Python 读取方式是 `np.unpackbits(np.frombuffer(bytes.fromhex(s), dtype=np.uint8), bitorder="little")[:52].reshape(13, 4)`，轴顺序为从 60 分钟前到当前的槽位 × 训练输入信号。C 的第 0 次沿用第三阶段 RNG 从全压力测试样本产生的对应掩码。重复 1–29 逐次记录种子。B 记录训练供体的隔间、岩棉板和原时标。局部归因数组 `phi_60/120` 形状为条件实现 × 样本 × 64 特征；`bias_60/120` 是树基值，`pred_60/120` 是加回当前 WC 后的预测。测试样本顺序由数组的 `sample_id` 和清单核对。各组 signed 和 abs 同样保留。CSV 中空指标代表定义域不足，例如全并列的排序相关或支持不足的区间。

已在独立目录完整复跑。两个 Booster、样本及掩码、归因数组、全部结果表和九张图的 SHA-256 均与交付文件一致。
