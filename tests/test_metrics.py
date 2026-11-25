"""Tests for metrics.metrics (v2 B-layer indicators).

Point estimates are checked against hand-computed values; bootstrap CIs
are checked for containment and determinism (fixed seed -> same CI).
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from metrics import metrics

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_CSV = REPO_ROOT / "examples" / "synthetic_runs.csv"


def _row(run_id, engine, prompt_id, mentioned, cited, position, recommended,
         shortlisted, cited_words, total_citations, all_brand_mentions):
    return {
        "run_id": run_id, "engine": engine, "prompt_id": prompt_id,
        "brand_mentioned": mentioned, "brand_cited": cited,
        "position": position, "recommended": recommended,
        "shortlisted": shortlisted, "cited_words": cited_words,
        "total_citations": total_citations,
        "all_brand_mentions": all_brand_mentions,
    }


# Hand-built fixture: 5 runs, 2 engines, 2 prompts, 1 ghost citation (R3).
ROWS = [
    _row("R1", "chatgpt", "Q01", 1, 1, 1, 1, 1, 100, 5, 3),
    _row("R2", "chatgpt", "Q01", 1, 1, 2, 0, 1, 80, 4, 2),
    _row("R3", "chatgpt", "Q02", 0, 1, None, 0, 0, 50, 6, 4),
    _row("R4", "perplexity", "Q01", 1, 0, 4, 0, 0, 0, 12, 5),
    _row("R5", "perplexity", "Q02", 0, 0, None, 0, 0, 0, 10, 3),
]


class MetricsTests(unittest.TestCase):
    def test_mention_rate(self):
        result = metrics.mention_rate(ROWS, n_boot=500, seed=1)
        self.assertEqual(result["value"], 0.6)
        self.assertEqual(result["n"], 5)
        self.assertLessEqual(result["ci95"][0], 0.6)
        self.assertGreaterEqual(result["ci95"][1], 0.6)

    def test_share_of_voice(self):
        # 3 mentions / (3+2+4+5+3) = 3/17
        result = metrics.share_of_voice(ROWS, n_boot=500, seed=1)
        self.assertEqual(result["value"], 0.1765)
        self.assertEqual(result["n"], 5)

    def test_citation_share(self):
        # 3 citations / (5+4+6+12+10) = 3/37
        result = metrics.citation_share(ROWS, n_boot=500, seed=1)
        self.assertEqual(result["value"], 0.0811)

    def test_ghost_citation_rate(self):
        # 3 cited runs, 1 ghost (R3) -> 1/3
        result = metrics.ghost_citation_rate(ROWS, n_boot=500, seed=1)
        self.assertEqual(result["value"], 0.3333)
        self.assertEqual(result["n"], 3)

    def test_pawc(self):
        # R1: 100 * 0.5**0 = 100; R2: 80 * 0.5**1 = 40; mean = 70.
        # R3 is a ghost (no position) and must not enter PAWC.
        result = metrics.pawc(ROWS, n_boot=500, seed=1)
        self.assertEqual(result["value"], 70.0)
        self.assertEqual(result["n"], 2)

    def test_mean_position_and_top3(self):
        mean = metrics.mean_position(ROWS, n_boot=500, seed=1)
        self.assertEqual(mean["value"], 2.3333)
        self.assertEqual(mean["n"], 3)
        top3 = metrics.top3_rate(ROWS, n_boot=500, seed=1)
        self.assertEqual(top3["value"], 0.6667)

    def test_recommendation_and_shortlist(self):
        self.assertEqual(metrics.recommendation_rate(ROWS, n_boot=100, seed=1)["value"], 0.2)
        self.assertEqual(metrics.shortlist_rate(ROWS, n_boot=100, seed=1)["value"], 0.4)

    def test_bootstrap_ci_is_deterministic(self):
        first = metrics.bootstrap_ci([0.0, 1.0, 1.0, 0.0, 1.0], n_boot=1000, seed=7)
        second = metrics.bootstrap_ci([0.0, 1.0, 1.0, 0.0, 1.0], n_boot=1000, seed=7)
        self.assertEqual(first, second)
        self.assertLessEqual(first[0], 0.6)
        self.assertGreaterEqual(first[1], 0.6)

    def test_empty_input_returns_none_not_zero(self):
        for fn in (metrics.mention_rate, metrics.share_of_voice,
                   metrics.citation_share, metrics.ghost_citation_rate,
                   metrics.pawc, metrics.mean_position, metrics.top3_rate):
            with self.subTest(fn=fn.__name__):
                result = fn([], n_boot=100, seed=1)
                self.assertIsNone(result["value"])
                self.assertEqual(result["ci95"], [None, None])

    def test_summarize_and_by_engine(self):
        summary = metrics.summarize(ROWS, n_boot=100, seed=1)
        self.assertEqual(summary["n_runs"], 5)
        self.assertEqual(summary["n_prompts"], 2)
        self.assertEqual(summary["engines"], ["chatgpt", "perplexity"])
        self.assertEqual(summary["b1_mention_rate"]["value"], 0.6)
        by_engine = metrics.summarize_by_engine(ROWS, n_boot=100, seed=1)
        self.assertEqual(set(by_engine), {"chatgpt", "perplexity"})
        self.assertEqual(by_engine["chatgpt"]["b1_mention_rate"]["value"], 0.6667)
        self.assertEqual(by_engine["perplexity"]["b1_mention_rate"]["value"], 0.5)

    def test_load_runs_synthetic_csv(self):
        rows = metrics.load_runs(str(SYNTHETIC_CSV))
        self.assertEqual(len(rows), 60)
        self.assertEqual({r["engine"] for r in rows}, {"chatgpt", "perplexity"})
        self.assertEqual(len({r["prompt_id"] for r in rows}), 10)

    def test_load_runs_rejects_missing_column(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["run_id", "engine"])
            writer.writerow(["R1", "chatgpt"])
            bad_path = handle.name
        with self.assertRaises(ValueError):
            metrics.load_runs(bad_path)
        Path(bad_path).unlink()

    def test_cli_smoke_on_synthetic_csv(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = metrics.main([str(SYNTHETIC_CSV), "--boot", "200"])
        self.assertEqual(code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["n_runs"], 60)
        self.assertIn("b1_mention_rate", report)
        self.assertIn("b2_ghost_citation_rate", report)
        self.assertIn("b4_pawc", report)


if __name__ == "__main__":
    unittest.main()
