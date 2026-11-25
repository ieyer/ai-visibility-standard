# Negency AI Visibility Whitepaper v1.1 — English Executive Summary

> The full text of this whitepaper is published in Chinese. This file is the English executive summary, contents, and key figures. Full text (Chinese): https://www.negency.com/zh/reports/ai-visibility-whitepaper/ — PDFs (Chinese full text + English summary) are available for download on the whitepaper pages.

*The full text of this whitepaper is published in Chinese. This page provides the English executive summary, the contents, and the key figures. The English PDF available for download on this page contains this summary; the complete Chinese text and its PDF are on the [Chinese page](https://www.negency.com/zh/reports/ai-visibility-whitepaper/).*

## Executive summary

On 24 November 2026, Negency's own website scored **100/100** — full marks in all five categories — on its own AI visibility audit. The previous day (23 November), the weekly retest of Negency's fixed buyer prompt set showed the brand's mention rate in AI answers was only **21.9%** (Negency's own measurement). A "perfect" website appears in AI answers only one time in five.

*Measurement basis for the 21.9% figure (weekly panel snapshot, not a v2 full sample): 16 prompts (8 Chinese, 8 English) × 2 engines (ChatGPT, Perplexity) × 1 run each = 32 samples, 23 November 2026; mention rate 7/32 = 21.9%, citation rate 9/32 = 28.1%, accuracy 7/7 = 100%. Each prompt was run once only — below the v2 requirement of ≥ 7 runs per prompt per day — so no confidence interval is reported, and the logged-in sessions may have inflated the level through personalisation. See the full disclosure block in the Chinese text (§执行摘要).*

This contradiction is not a data error; the standard measures the wrong object. Current AI visibility audits test whether a website is "ready to be read" — does it have schema, does it have llms.txt, does it have social links — not whether it is "read, cited, and recommended". The former is readiness hygiene; the latter is visibility. A September 2026 preprint put a number on the relationship: the Spearman correlation between content audit scores and actual citation rates is approximately **0.11**, explaining about 1% of the variance (Bajemon & Rochet 2026, preprint, [arXiv:2609.07559](https://arxiv.org/abs/2609.07559)). A perfect-scoring website barely predicts whether AI will cite it.

This whitepaper argues three points:

**First, the industry has no agreed AI visibility standard.** IETF, W3C, and schema.org have no GEO scoring standard; identically named metrics use different formulas at different vendors and cannot be compared; of 11 vendors' official documentation, not one publishes a confidence interval or error range for its headline score (SEOptimize 2026-08, independent review). A number without uncertainty is not a measurement; it is a performance.

**Second, citations do not happen on the brand's own website.** Only **2.9%** of citations point to a brand's own domain; 75.2% point to other companies in the same field (Kumar 2026, preprint; the author is a vendor co-founder, conflict of interest declared). The engine's own retrieval decides whether a page enters the citation candidate set, explaining **32%** of within-site citation variance, while all page features combined explain only **2.8%** (Chen & Koudas 2026, preprint). Optimising your own website fights over a 3% pool.

**Third, measurement must carry uncertainty, denominators, and time.** Ask the same question 100 times and the chance of getting the same brand list is under 1% (SparkToro 2026, independent review); 40–60% of cited domains rotate every month (Profound, vendor-reported). A single snapshot is a lottery ticket; a trend with confidence intervals is a fact.

On this basis, the whitepaper proposes the **Negency AI Visibility Standard v2**: no blended total score, in a three-layer structure — Layer A, binary gates (pass/fail, unscored); Layer B, eight outcome dimensions (mention, citation, influence, position, accuracy, sentiment, retrieval eligibility, third-party footprint, each with a confidence interval); Layer C, conditional dimensions (applicability tested first; "not applicable" is reported, not scored zero), plus a China engine panel (pilot) and five sampling disciplines (frozen prompt set, at least 7 runs per prompt per day, bootstrap confidence intervals, per-engine reporting, full disclosure block).

The whitepaper is also self-criticism: the v1 audit is exactly the kind of standard this paper criticises — it gave us 100 points, and a false sense of security. v2 starts by admitting that mistake and fixing it. The standard's methodology and reference implementation code are published openly at [github.com/ieyer/ai-visibility-standard](https://github.com/ieyer/ai-visibility-standard), so anyone can verify, reproduce, or refute them.

## Contents

- Executive summary

- Chapter 1 — There is no standard: no agreed definition, same name different numbers, no uncertainty, the perfect-score trap, the real state of standards work

- Chapter 2 — What actually drives citations: retrieval as gatekeeper, third parties as the main battlefield, absorption over selection, evidence density over format, ghost citations, the brand ladder, time and rotation, the two universes (Chinese engines), the overrated checklist

- Chapter 3 — The Negency Standard v2: five design principles, Layer A binary gates, Layer B eight outcome dimensions, Layer C conditional dimensions, the China panel (pilot), sampling and reporting discipline, v1 → v2 mapping

- Chapter 4 — From diagnosis to improvement: the value of diagnosis, gap → remediation mapping, selling re-measurement not reports, no guarantees on outcomes, time expectations, falsification conditions, first-week three actions, v2 sample report (illustrative)

- Appendix A — Methodology disclosure (evidence sources, evidence grades, limitations, reference implementation, versioning, complete standards-activity list)

- Appendix B — Glossary (22 terms)

- Appendix C — References (54 sources: academic literature, vendor documentation, independent reviews, standards and engine documentation)

## The v2 standard at a glance

**Layer A — binary gates (pass / fail, unscored).** A1 AI crawler access; A2 server-side rendering; A3 basic reachability; A4 entity consistency; A5 contact and trust; A6 answerable content. llms.txt, JSON-LD, FAQ blocks, and summary boxes are advisory items, not gates — the evidence shows they do not drive citations.

**Layer B — eight outcome dimensions (core, each with a confidence interval).** B1 mention rate and share of voice; B2 citation share and citation quality (including ghost-citation rate and co-citation); B3 citation influence (absorption); B4 position and recommendation; B5 factual accuracy (claim-level audit with upstream tracing); B6 sentiment and narrative (including the sentiment–recommendation gap); B7 retrieval eligibility and evidence density; B8 third-party mention footprint.

**Layer C — conditional dimensions.** C1 conversion and traffic (only with analytics access); C2 agentic commerce readiness (shopping trigger rate tested first; zero means "not applicable"); C3 freshness and latency (time-to-first-citation distribution, never-cited pages counted in the denominator).

**China panel (pilot, scored separately).** Doubao, DeepSeek, Qwen, Yuanbao, Kimi, ERNIE Bot; app and web sampled separately. Delivery feasibility was unverified at publication, so the panel is labelled a pilot, not sold as a committed service.

## Key figures

| Finding | Figure | Source |
| --- | --- | --- |
| Content score vs citation rate (Spearman ρ) | ≈ 0.11 (~1% of variance) | Bajemon & Rochet 2026, preprint |
| Citations pointing to the brand's own domain | 2.9% | Kumar 2026, preprint |
| Within-site citation variance: engine retrieval vs all page features | 32% vs 2.8% | Chen & Koudas 2026, preprint |
| Ghost citations (domain listed, brand absent from the answer) | 61.7% | Semrush 2026-06, vendor-reported |
| First-round non-branded visibility: household / mid-size / niche brands | 72.9% / 43.6% / 11.4% | Kumar 2026, preprint |
| Cited domains rotating each month | 40–60% | Profound, vendor-reported |
| Median time to first citation: cited pages only vs Kaplan-Meier | 6.81 days vs 42 days | Profound / LoudFace |
| Company websites' share of citations: Chinese vs Western engines | 4.33–18.3% vs ~57% | Sanbi 2026-10, independent review |
| Minimum sampling per prompt per day | ≥ 7 runs | Schulte et al. 2026, preprint |

## How to cite

Negency. *AI Visibility Whitepaper v1.1: From Readiness Hygiene to Outcome Measurement — An Auditable Standard for AI Visibility.* 25 November 2026. Full text (Chinese): [https://www.negency.com/zh/reports/ai-visibility-whitepaper/](https://www.negency.com/zh/reports/ai-visibility-whitepaper/)

Version 1.1, 25 November 2026. Published by Negency (Shanghai Negency Information Technology Co., Ltd.). The whitepaper is a living document: the standard is frozen at publication date, and revisions with a changelog follow when the evidence updates.
