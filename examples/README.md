# 示例数据说明

`synthetic_runs.csv` 是**合成数据**：由 `generate_synthetic.py`（固定种子 42）
生成的 60 行虚构采样 run，仅用于演示指标模块的用法与输出格式。

- 引擎名（chatgpt / perplexity）是真实的，但所有提及、引用、位置数字均为随机生成。
- `prompt_id`（Q01–Q10）为占位编号，不对应任何真实题集。
- 本仓库不含任何真实面板测量数据。

重新生成：`python examples/generate_synthetic.py`
