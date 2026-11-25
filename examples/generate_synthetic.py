"""生成合成示例采样数据（synthetic_runs.csv）。

本脚本用固定种子生成完全虚构的采样 run，仅用于演示指标模块的
用法与输出格式。**数据中没有任何真实测量结果**：引擎名是真实的，
但所有提及/引用/位置数字均为随机生成；prompt_id（Q01–Q10）为占位编号，
不对应任何真实题集。

重新生成：python examples/generate_synthetic.py
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

SEED = 42
ENGINES = ("chatgpt", "perplexity")
PROMPTS = [f"Q{i:02d}" for i in range(1, 11)]
RUNS_PER_PROMPT = 3

# 各引擎的合成行为参数（虚构，仅为演示合理的数量级）
PARAMS = {
    "chatgpt": {"p_mention": 0.40, "p_cite_if_mentioned": 0.80, "p_ghost": 0.08,
                "citations": (4, 9)},
    "perplexity": {"p_mention": 0.30, "p_cite_if_mentioned": 0.70, "p_ghost": 0.10,
                   "citations": (10, 18)},
}

HEADER = [
    "run_id", "engine", "prompt_id", "brand_mentioned", "brand_cited",
    "position", "recommended", "shortlisted", "cited_words",
    "total_citations", "all_brand_mentions",
]


def generate(path: Path) -> int:
    rng = random.Random(SEED)
    rows = []
    run_no = 0
    for engine in ENGINES:
        params = PARAMS[engine]
        for prompt_id in PROMPTS:
            for _ in range(RUNS_PER_PROMPT):
                run_no += 1
                mentioned = 1 if rng.random() < params["p_mention"] else 0
                if mentioned:
                    cited = 1 if rng.random() < params["p_cite_if_mentioned"] else 0
                else:
                    cited = 1 if rng.random() < params["p_ghost"] else 0
                position = rng.choices([1, 2, 3, 4, 5], weights=[4, 3, 2, 1, 1])[0] if mentioned else ""
                recommended = 1 if mentioned and rng.random() < 0.15 else 0
                shortlisted = 1 if mentioned and rng.random() < 0.25 else 0
                cited_words = rng.randint(20, 120) if cited else 0
                total_citations = rng.randint(*params["citations"])
                all_brand_mentions = rng.randint(2, 6)
                rows.append([
                    f"R{run_no:03d}", engine, prompt_id, mentioned, cited,
                    position, recommended, shortlisted, cited_words,
                    total_citations, all_brand_mentions,
                ])
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    out = Path(__file__).with_name("synthetic_runs.csv")
    count = generate(out)
    print(f"wrote {count} synthetic runs to {out}")
