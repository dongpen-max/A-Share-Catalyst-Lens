---
name: a-share-catalyst-lens
description: "Analyze China A-share and China-related stock catalysts, bullish or bearish news, policy events, announcements, earnings, fund flows, sector themes, sentiment, and price-volume confirmation with fresh citations, confidence levels, and explicit uncertainty. Use for requests about China stocks, A-shares, lihao/li kong, catalysts, themes, limit-up moves, policy, earnings, capital flow, price impact, or event-driven stock analysis."
---

# A-Share Catalyst Lens

## Overview

Use this skill to turn scattered China stock news and market data into citation-first catalyst analysis with a bullishness score, confidence level, and invalidation checks.

## Core Workflow

1. Clarify the stock, sector, event, time window, market scope, and user horizon. Default to Chinese output for China A-share requests.
2. Gather fresh evidence before judging the catalyst. Use official filings, exchange or regulator pages, company announcements, reputable financial media, and current market data.
3. Keep automatically discovered evidence pending until the user or analyst verifies and accepts it. Do not let unreviewed retrieval results affect the score.
4. Build an event ledger separating facts, source links, interpretation, price reaction, counterevidence, and missing data.
5. Classify each catalyst as policy, earnings, order or contract, buyback or dividend, M&A or restructuring, product approval, capital flow, sector theme, rumor, or negative risk.
6. Score bullishness with source reliability, materiality, immediacy, novelty, confirmation, market alignment, priced-in risk, and counterevidence.
7. Validate the conclusion against price-volume behavior, sector and index context, peers, fundamentals, and historical base rates when enough data exists.
8. Return a concise Chinese report with citations, catalyst score, evidence confidence, coverage gaps, risk triggers, monitoring checklist, and a no-financial-advice caveat.

## Resources

- Read `references/catalyst-rubric.md` for the scoring rubric, event ledger fields, and report template.
- Read `references/data-sources.md` when choosing China stock data sources and evidence priority.
- Use `scripts/catalyst_score.py` when the user provides or you build structured event evidence.
- Run `python -m server` when a local auditable evidence workspace and CNINFO announcement discovery are useful.

## Validation

- Never claim guaranteed accuracy or imply a sure trading outcome.
- Use current sources for dates, prices, filings, policy changes, and market reactions.
- Cite every material claim and label inference separately from evidence.
- Mark rumors or single-source claims as unverified and lower confidence.
- Keep catalyst strength, evidence confidence, and source coverage as separate outputs.
- If data is missing, stale, or unavailable, say exactly what could not be verified.
