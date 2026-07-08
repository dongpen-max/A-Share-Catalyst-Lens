# China Stock Data Sources

## Evidence Priority

1. Official and primary: company announcements, exchange filings, CNINFO, SSE, SZSE, HKEX, CSRC, PBOC, NDRC, MOF, MIIT, SAMR, company investor relations.
2. Market data: exchange data, licensed terminal data, AKShare, TuShare Pro, Qlib local datasets, RQData/JQData/Wind/iFinD if the user has access.
3. Reputable financial media: Xinhua, Securities Times, China Securities Journal, Shanghai Securities News, Yicai, Caixin, Eastmoney, Sina Finance, brokerage research when source and date are clear.
4. Secondary and social: forums, social media, unsourced screenshots, or reposted rumors. Use only for sentiment context, never as the sole basis for a bullish conclusion.

## Practical Source Selection

- Prefer AKShare when no token is available and public data is enough.
- Prefer TuShare Pro when `TUSHARE_TOKEN` is configured or the user needs structured A-share fundamentals, macro, index, or daily market data.
- Prefer Qlib-style local datasets when the task needs ML-style factor validation, rolling checks, or benchmark discipline.
- Prefer local-first backtesting or event-study workflows when the user asks for strategy validation, execution constraints, or historical base rates.
- Prefer retrieval-backed financial NLP patterns for news sentiment, event summarization, and evidence-grounded reasoning; adapt to Chinese sources and cite the retrieval evidence.

## Freshness Rules

- Always verify today's price, latest announcement, latest policy wording, and current market reaction with live sources.
- Use absolute dates in the report, especially when the user says today, yesterday, latest, or recently.
- If web or data access fails, continue with available evidence but mark the analysis as incomplete and lower confidence.

## A-Share Context Checks

- Compare the stock with its sector index, CSI 300/CSI 500/ChiNext/STAR Market context when relevant.
- Check whether the catalyst affects revenue, margin, valuation multiple, liquidity, or only narrative attention.
- Look for pre-existing price moves, limit-up streaks, high turnover, abnormal volume, or crowded theme behavior.
- Check financial quality: revenue growth, net profit, gross margin, cash flow, leverage, goodwill, receivables, and pledge/dilution risk when available.
- Check event mechanics: trading halt/resumption, ST risk, lock-up expiry, refinancing, shareholder reduction, M&A uncertainty, exchange inquiry letters, and regulatory approvals.
