# Negency AI 可见性标准 v2 —— 参考实现

本仓库是《Negency AI 可见性白皮书》所定义标准的**参考实现**：方法论公开、公式公开、代码公开，任何人都可以复核、复现、反驳。

> 白皮书即将发布。本仓库的完整标准文档见 [STANDARD.md](STANDARD.md)。

## 标准概览

v2 把 AI 可见性测量从"网站准备度检查"升级为"AI 回答中的结果测量"，不设混合总分，只给带置信区间的剖面。

**五条设计原则**：测结果，不测 artifact；所有数字带不确定度；逐引擎、逐语言、逐界面，不混合；每个缺口对应整改动作；保证方法，不保证结果。

| 层 | 内容 | 计分 |
|---|---|---|
| A 层 | 二值门槛：爬虫可达、服务端渲染、基础可达、实体口径、联系与信任、内容可答 | 通过 / 不通过，不计分 |
| B 层 | 结果八维：提及率与声量份额、引用份额与质量、引用影响、位置与推荐、事实准确度、情感与叙事、检索资格与证据密度、第三方提及足迹 | 每维带 bootstrap 95% CI |
| C 层 | 条件维度：转化与流量、Agentic Commerce 就绪度、新鲜度与延迟 | 先测适用性，不适用则标注而非打 0 分 |

**采样纪律**（违反则报告无效）：冻结题集版本化；每题每天 ≥7 次采样；所有比率带 bootstrap 95% CI；逐引擎、逐模型版本、逐语言、逐界面报告；每份报告附完整披露块（题集哈希、引擎与模型版本、采集面、采样次数、日期、分母定义）。

## 仓库结构

```
├── STANDARD.md            标准全文（A/B/C 三层、采样纪律、缺口→整改映射）
├── audit_engine/          v1 体检引擎（独立版，纯标准库）
│   └── audit.py           run_audit(domain)：约 30 项信号，五类加权评分
├── metrics/               v2 B 层结果指标计算（纯标准库）
│   └── metrics.py         提及率、SoV、引用份额、幽灵引用率、PAWC 等 + bootstrap CI
├── examples/
│   ├── synthetic_runs.csv 合成示例采样数据（60 行，完全虚构）
│   └── generate_synthetic.py  合成数据生成器（固定种子，可复现）
└── tests/                 离线测试（审计引擎 7 项 + 指标 13 项）
```

## 快速开始

零依赖，Python 3.10+ 即可运行。

**体检一个网站**（v1 引擎，A 层门槛的自动化检查）：

```bash
python -m audit_engine example.com
```

输出 JSON：`total_score`（0–100）、五类分项、`top_issues`（最严重的 5 个问题）、`fetched_at`。站点不可达时返回 `total_score: null` 与错误说明，不抛异常。

**计算 B 层指标**（v2，输入为采样 runs 的 CSV，格式见 `metrics/metrics.py` 顶部的文档）：

```bash
python -m metrics examples/synthetic_runs.csv            # 全部引擎汇总
python -m metrics examples/synthetic_runs.csv --by-engine  # 逐引擎分别报告
python -m metrics examples/synthetic_runs.csv --engine chatgpt
```

输出 JSON：每项指标含 `value`、`n`（分母）、`ci95`（bootstrap 95% 置信区间）。样本为空时指标值为 `null`——不编造数字。

**跑测试**（全部离线，不触网）：

```bash
python -m unittest discover -s tests -v
```

## 方法论要点

- **没有混合总分。** B 层八维分开报告，权重不公开的总分把"哪弱"藏起来了。
- **没有不确定度的比率不进报告。** 所有比率带 bootstrap 95% CI；变化不跨过噪声 floor 不报"上升/下降"。
- **逐引擎报告。** 跨引擎域名重叠极低（Jaccard 约 0.10–0.25），混合数字没有测量意义。
- **示例数据是合成的。** `examples/synthetic_runs.csv` 由固定种子随机生成，仅演示用法；本仓库不含任何真实面板测量数据。
- **v1 引擎的定位。** `audit_engine` 是 v1"准备度卫生"体检的实现；v2 中它降级为 A 层门槛检查，不再作为可见度的代理指标。引擎取自 Negency 官网仓库（NegencyPro，提交 f065726，`core/audit.py`），唯一改动是去掉 Django 依赖（改用标准库 `datetime`），评分逻辑与行为完全一致。

## 许可证

MIT，见 [LICENSE](LICENSE)。

Negency（上海熵宜信息技术有限公司）· https://www.negency.com
