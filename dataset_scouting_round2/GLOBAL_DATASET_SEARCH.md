# 全球公开农业时序数据集调查

检索截至 **2026-11-07**。先读取第一阶段 [最终决定](../greenhouse_tomato_data_audit/FINAL_DECISION.md)、[审查](../greenhouse_tomato_data_audit/DATA_AUDIT_REPORT.md) 和 [相关研究](../greenhouse_tomato_data_audit/RELATED_WORK.md)。首轮压力室数据仍只有三个有效水势日，本轮将其作为背景和重复检测来源，不计为新增候选。

## 检索和筛选

用 `(tomato OR cucumber OR capsicum OR strawberry OR lettuce) AND (greenhouse OR controlled environment)` 与 `water potential`、`dendrometer`、`sap flow`、`plant electrophysiology`、`transpiration`、`substrate water content`、`irrigation`、`phenotyping`、`dataset` 和 `data availability` 交叉。扩大到葡萄和液流研究以检验直接生理标签与外部测试的边界。候选来自欧洲、亚洲、北美、南美和大洋洲。此为带证据等级的定向调查，范围按下表记录。

| 来源 | 实际检索路径 | 筛选处置 |
|---|---|---|
| Mendeley Data | 指定 DOI，另查番茄茎径、根区和葡萄压力室。对候选核实版本、说明和下载 | Parma 两库与比利时档案实际下载，另保留里斯本葡萄园证据 |
| Zenodo | 以数据论文与作者的 Data Availability 回查 DOI、API、同一概念版本链、文件大小与成员 | 电生理、Horti、生菜、草莓、叶水势、NaPPI 及 SAPFLUXNET 等进入筛选 |
| Figshare / 4TU | 查 WUR 的三个挑战 DOI 和 Figshare 的历史文件元数据，再查 4TU 现行下载文件 | 番茄归档成功下载并验 MD5；黄瓜和生菜挑战做文献级筛选 |
| Dryad | 定向搜温室番茄灌溉、蒸腾和气体交换，查 README 与样本变量 | 保留春番茄和气体交换响应曲线，低频或短实验限制正式时序 |
| Harvard Dataverse | 域限定检索 greenhouse / irrigation / plant water status | 未找到可核验且进入本轮的同步植物生理和高频流档案 |
| IEEE DataPort | 域限定检索 greenhouse / irrigation / time series；另查 IEEE Xplore 论文 | 搜索工具报告 DataPort robots 限制。Xplore 仅支持文献重合判断，不用论文摘要假定有公开数据 |
| Kaggle | 看温室、农业土壤与标签数据卡及上游来源 | 常见 AGC 派生集回溯 4TU，合成或上游不清晰的表排除 |
| GitHub | 查 CEAOD、UdeA 数据字典、TS_Protocol 和论文仓库 | 有 DOI 的原始源优先，GitHub 派生或说明与同一记录合并，导航数据不算植物监督 |
| 大学、研究机构 | Parma IRIS、WUR、Aalto/UC Davis、Helsinki NaPPI、WSU、BYU | 用研究机构的采集和文件说明消除镜像重复，访问权限不明记待核 |
| Scientific Data / Data in Brief | 阅读 Horti、Parma、PC4C 数据论文及 Data Availability，逆查原始仓库 | 区分 raw_data、处理版、模拟目标、累计量和采收量 |
| 生理论文 | 沿 PlantArray、茎水势比较、PSY1/TEROS21 协议、SAPFLUXNET 回查公开原始观测 | PlantArray 多年两温室和 Plant Physiology 四番茄极端干旱数据均为按合理请求获取，排除在公开候选计数外 |

## 数量口径

- **22 个候选数据系列**。初始六项全部保留在清单中，做了重叠检查。Evolving 和 Parma 旧版列为两个发布记录，但只对应一个 2023 活动。电生理两个版本合为一个系列；不同作物/不同挑战活动是不同候选。
- **7 个**有直接的植物水分关系或气体交换实测候选目标。包括叶/茎水势、液流、蒸腾或气孔导度。若用叶温、茎径、SPAD、NDVI 等作为胁迫代理，应另列。电生理确实测得电位，但它的拟用分类标签源自处理阶段，因此没有纳入这 7 个。7 项中实际读了 TS_Protocol 与 Kannenberg 原表，其余为官方说明或原论文证据。
- **13 个**有文件或作者文档支持的亚小时至小时连续记录段，连续筛查下限为两天。允许真实断档，记录可以仅为环境或根区；连续输入与连续生理目标分开。未核实的连续性记 unknown。
- **8 个**有足以设计各自受限任务的分组测试单位。这里要求至少能构造分组训练、验证和测试且目标与时间覆盖与任务相符。包含单地内季/隔间、低频的植物/空间组和跨生态地点的通用方法数据；此数是结构性筛选，不能作统计功效承诺。其中 Evolving、WUR 番茄、Horti 和 Mobile 已用原件核实单位；其余四个目前基于官方说明或论文。电生理虽然有 16 个可辨认植株和 2 个植株留出，单批和较小测试数量不足以计入这 8 个正式候选。

完整的纳入、排除、许可及查阅等级可逐行复算。字段 `file` 表示实际读取相应原件或作者发布的指定子集，`paper` 表示原论文/正式数据论文的证据，`catalog` 表示官方条目或 README。论文按请求获取、未证上游的 Kaggle 表、视觉导航和模拟环境数据不计入 22 项。 [完整清单](dataset_inventory.csv) · [计分表](dataset_feasibility_scores.csv) · [深入审查](TOP3_DATASET_AUDIT.md)

本轮可下载结果包括完整原包、已核验的原始子表和仅审查文件目录三类。对 Horti 和电生理大包，使用官方 HTTP 206 及 ZIP 中央目录进行选择性获取，逐成员核对压缩内容解码和 CRC。保留官方版本，不替换成第三方重制数据。 [PlantArray 的数据声明](https://pmc.ncbi.nlm.nih.gov/articles/PMC12675978/) · [番茄/葡萄极端干旱研究的数据声明](https://doi.org/10.1093/plphys/kiaf591)
