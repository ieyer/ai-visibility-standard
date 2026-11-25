"""Negency AI 可见性标准 v2 —— 结果指标计算模块.

从一次采样运行（runs）的 CSV 计算 B 层结果指标，所有比率附
bootstrap 95% 置信区间。本模块是标准的参考实现：公式见 STANDARD.md，
任何人可用自己的采样数据复现同样的数字。

输入 CSV 格式（每行 = 一个问题 × 一个引擎 × 一轮采样）::

    run_id,engine,prompt_id,brand_mentioned,brand_cited,position,
    recommended,shortlisted,cited_words,total_citations,all_brand_mentions

    run_id             本轮采样的唯一编号
    engine             引擎名，如 chatgpt / perplexity（逐引擎报告，不混合）
    prompt_id          冻结题集中的问题编号，如 Q01
    brand_mentioned    品牌名是否出现在答案正文中（0/1）
    brand_cited        品牌域名是否出现在引用来源列表中（0/1）
    position           品牌首次提及的位置（1 起；未提及留空）
    recommended        答案是否主动推荐该品牌（0/1）
    shortlisted        品牌是否进入答案的对比短名单（0/1）
    cited_words        归因于品牌被引句子的答案词数（未被引为 0）
    total_citations    本答案的引用总数
    all_brand_mentions 本答案提到的不同品牌总数

指标定义（与 STANDARD.md 一致）::

    提及率 (B1)     sum(brand_mentioned) / n_runs
    声量份额 SoV (B1) sum(brand_mentioned) / sum(all_brand_mentions)
    引用份额 (B2)   sum(brand_cited) / sum(total_citations)
    幽灵引用率 (B2)  在 brand_cited=1 的 run 中，brand_mentioned=0 的占比
    PAWC (B4)       在被引且被提及的 run 上，cited_words * 0.5**(position-1) 的均值
                    （位置加权词数，Aggarwal et al. KDD 2024；本实现采用
                    0.5 为底的指数折扣，position 为品牌首次提及位置）
    平均位置 (B4)   被提及 run 的 position 均值
    Top-3 率 (B4)   被提及 run 中 position <= 3 的占比
    推荐率 (B4)     sum(recommended) / n_runs
    短名单率 (B4)   sum(shortlisted) / n_runs

置信区间：百分位 bootstrap（默认 10,000 次重采样，固定种子可复现）。
比率类指标对 0/1 标志重采样；SoV 与引用份额对 run 重采样后重算比值。
样本为空时指标值为 None，CI 为 [None, None]——不编造数字。

纯标准库，零依赖。用法::

    python -m metrics examples/synthetic_runs.csv
    python -m metrics examples/synthetic_runs.csv --by-engine
    python -m metrics examples/synthetic_runs.csv --engine chatgpt
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys

DEFAULT_BOOT = 10_000
DEFAULT_SEED = 20261124

# CSV 列定义：(列名, 类型)。position 为空字符串时记为 None。
_INT_COLUMNS = (
    "brand_mentioned",
    "brand_cited",
    "recommended",
    "shortlisted",
    "cited_words",
    "total_citations",
    "all_brand_mentions",
)
_REQUIRED_COLUMNS = (
    "run_id",
    "engine",
    "prompt_id",
    *_INT_COLUMNS,
    "position",
)


def load_runs(path: str) -> list[dict]:
    """读取采样 CSV，返回类型化后的 run 字典列表."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: 空文件或缺少表头")
        missing = [col for col in _REQUIRED_COLUMNS if col not in reader.fieldnames]
        if missing:
            raise ValueError(f"{path}: 缺少列: {', '.join(missing)}")
        runs = []
        for line_no, row in enumerate(reader, start=2):
            try:
                typed: dict = {
                    "run_id": row["run_id"],
                    "engine": row["engine"],
                    "prompt_id": row["prompt_id"],
                }
                for col in _INT_COLUMNS:
                    typed[col] = int(row[col])
                position = (row["position"] or "").strip()
                typed["position"] = int(position) if position else None
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{line_no}: 数值解析失败: {exc}") from exc
            runs.append(typed)
    return runs


def bootstrap_ci(
    values: list[float],
    n_boot: int = DEFAULT_BOOT,
    seed: int = DEFAULT_SEED,
) -> list[float | None]:
    """百分位 bootstrap 95% CI（均值）。空输入返回 [None, None]."""
    if not values:
        return [None, None]
    rng = random.Random(seed)
    n = len(values)
    replicates = []
    for _ in range(n_boot):
        replicates.append(sum(rng.choice(values) for _ in range(n)) / n)
    replicates.sort()
    lo = replicates[max(0, int(0.025 * n_boot) - 1)]
    hi = replicates[min(n_boot - 1, int(0.975 * n_boot))]
    return [round(lo, 4), round(hi, 4)]


def bootstrap_ratio_ci(
    numerators: list[float],
    denominators: list[float],
    n_boot: int = DEFAULT_BOOT,
    seed: int = DEFAULT_SEED,
) -> list[float | None]:
    """比值 sum(num)/sum(den) 的 bootstrap 95% CI（对 run 重采样）."""
    if not numerators or sum(denominators) == 0:
        return [None, None]
    rng = random.Random(seed)
    n = len(numerators)
    replicates = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        den = sum(denominators[i] for i in idx)
        if den == 0:
            continue
        replicates.append(sum(numerators[i] for i in idx) / den)
    if not replicates:
        return [None, None]
    replicates.sort()
    m = len(replicates)
    lo = replicates[max(0, int(0.025 * m) - 1)]
    hi = replicates[min(m - 1, int(0.975 * m))]
    return [round(lo, 4), round(hi, 4)]


def _metric(value: float | None, n: int, ci: list[float | None]) -> dict:
    return {
        "value": round(value, 4) if value is not None else None,
        "n": n,
        "ci95": ci,
    }


def _rate_metric(flags: list[int], n_boot: int, seed: int) -> dict:
    if not flags:
        return _metric(None, 0, [None, None])
    rate = sum(flags) / len(flags)
    return _metric(rate, len(flags), bootstrap_ci([float(f) for f in flags], n_boot, seed))


def mention_rate(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B1 提及率：提到品牌的 runs ÷ 全部 runs."""
    return _rate_metric([r["brand_mentioned"] for r in rows], n_boot, seed)


def share_of_voice(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B1 声量份额 SoV：品牌提及数 ÷ 全部品牌提及数."""
    num = [float(r["brand_mentioned"]) for r in rows]
    den = [float(r["all_brand_mentions"]) for r in rows]
    if not rows or sum(den) == 0:
        return _metric(None, len(rows), [None, None])
    return _metric(sum(num) / sum(den), len(rows), bootstrap_ratio_ci(num, den, n_boot, seed))


def citation_share(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B2 引用份额：引用品牌域名的次数 ÷ 全部引用数."""
    num = [float(r["brand_cited"]) for r in rows]
    den = [float(r["total_citations"]) for r in rows]
    if not rows or sum(den) == 0:
        return _metric(None, len(rows), [None, None])
    return _metric(sum(num) / sum(den), len(rows), bootstrap_ratio_ci(num, den, n_boot, seed))


def ghost_citation_rate(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B2 幽灵引用率：被引 run 中品牌名未出现在答案正文的占比."""
    cited = [r for r in rows if r["brand_cited"] == 1]
    ghosts = [1 if r["brand_mentioned"] == 0 else 0 for r in cited]
    return _rate_metric(ghosts, n_boot, seed)


def pawc(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B4 PAWC：位置加权词数（被引且被提及的 run 上取均值）.

    单 run PAWC = cited_words * 0.5**(position - 1)。幽灵引用（被引但未
    提及）没有提及位置，不计入 PAWC——它们由幽灵引用率单独度量。
    """
    values = [
        r["cited_words"] * (0.5 ** (r["position"] - 1))
        for r in rows
        if r["brand_cited"] == 1 and r["position"] is not None
    ]
    if not values:
        return _metric(None, 0, [None, None])
    mean = sum(values) / len(values)
    return _metric(mean, len(values), bootstrap_ci(values, n_boot, seed))


def mean_position(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B4 平均位置：被提及 run 的首次提及位置均值（1 起）."""
    values = [float(r["position"]) for r in rows if r["position"] is not None]
    if not values:
        return _metric(None, 0, [None, None])
    mean = sum(values) / len(values)
    return _metric(mean, len(values), bootstrap_ci(values, n_boot, seed))


def top3_rate(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B4 Top-3 率：被提及 run 中首次提及位置 <= 3 的占比."""
    mentioned = [r for r in rows if r["position"] is not None]
    flags = [1 if r["position"] <= 3 else 0 for r in mentioned]
    return _rate_metric(flags, n_boot, seed)


def recommendation_rate(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B4 推荐率：答案主动推荐品牌的 run 占比."""
    return _rate_metric([r["recommended"] for r in rows], n_boot, seed)


def shortlist_rate(rows: list[dict], n_boot: int = DEFAULT_BOOT, seed: int = DEFAULT_SEED) -> dict:
    """B4 短名单率：品牌进入答案对比短名单的 run 占比."""
    return _rate_metric([r["shortlisted"] for r in rows], n_boot, seed)


def summarize(
    rows: list[dict],
    engine: str | None = None,
    n_boot: int = DEFAULT_BOOT,
    seed: int = DEFAULT_SEED,
) -> dict:
    """对一组 run 计算全部 B 层指标。engine 给定时只计该引擎的 run."""
    if engine is not None:
        rows = [r for r in rows if r["engine"] == engine]
    return {
        "engine": engine or "all",
        "n_runs": len(rows),
        "n_prompts": len({r["prompt_id"] for r in rows}),
        "engines": sorted({r["engine"] for r in rows}),
        "b1_mention_rate": mention_rate(rows, n_boot, seed),
        "b1_share_of_voice": share_of_voice(rows, n_boot, seed),
        "b2_citation_share": citation_share(rows, n_boot, seed),
        "b2_ghost_citation_rate": ghost_citation_rate(rows, n_boot, seed),
        "b3_note": "引用影响（吸收度）需逐句影响分，不在本 CSV 口径内；见 STANDARD.md B3。",
        "b4_pawc": pawc(rows, n_boot, seed),
        "b4_mean_position": mean_position(rows, n_boot, seed),
        "b4_top3_rate": top3_rate(rows, n_boot, seed),
        "b4_recommendation_rate": recommendation_rate(rows, n_boot, seed),
        "b4_shortlist_rate": shortlist_rate(rows, n_boot, seed),
    }


def summarize_by_engine(
    rows: list[dict],
    n_boot: int = DEFAULT_BOOT,
    seed: int = DEFAULT_SEED,
) -> dict[str, dict]:
    """逐引擎分别计算（标准要求：禁止跨引擎混合）."""
    return {
        engine: summarize(rows, engine=engine, n_boot=n_boot, seed=seed)
        for engine in sorted({r["engine"] for r in rows})
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="计算 Negency AI 可见性标准 v2 的 B 层结果指标（带 bootstrap 95% CI）。"
    )
    parser.add_argument("csv", help="采样 runs 的 CSV 文件")
    parser.add_argument("--engine", default=None, help="只计算该引擎的 run")
    parser.add_argument("--by-engine", action="store_true", help="逐引擎分别输出")
    parser.add_argument("--boot", type=int, default=DEFAULT_BOOT, help="bootstrap 重采样次数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子（固定种子可复现）")
    args = parser.parse_args(argv)
    try:
        rows = load_runs(args.csv)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.by_engine:
        report = summarize_by_engine(rows, n_boot=args.boot, seed=args.seed)
    else:
        report = summarize(rows, engine=args.engine, n_boot=args.boot, seed=args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
