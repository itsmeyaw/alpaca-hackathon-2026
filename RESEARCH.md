# AI Trading Agent Research Brief

## Hackathon Objective

The project is judged on P&L in Alpaca paper trading, effective use of Alpaca's API/MCP/CLI, originality, and clear execution. The winning design should prioritize a robust live system and visible agent behavior over an impressive but unvalidated end-to-end model.

## Executive Recommendation

Build **Catalyst Router**: an event-driven, regime-aware mixture-of-experts agent that decides whether an unusual move is driven by new information or temporary liquidity/noise.

- A credible catalyst plus price and volume confirmation routes to a continuation strategy.
- An unexplained, extreme market-relative move routes to a mean-reversion strategy.
- Ambiguous, contradictory, illiquid, stale, or high-risk cases route to `NO_TRADE`.

The LLM performs structured news/event extraction and explains decisions. Deterministic quantitative code produces forecasts, sizes positions, enforces risk limits, and submits orders. This is materially more defensible than allowing an LLM to freely predict prices or execute arbitrary trades.

## What State of the Art Actually Says

### LLM agents

Multi-agent frameworks such as TradingAgents use specialized fundamental, sentiment, technical, bull/bear, trader, and risk roles, reporting stronger backtest results than simpler agents [1]. FinMem reports benefits from a layered memory that prioritizes information by relevance and timescale [2].

The strongest recent evidence is cautionary:

- AI-Trader finds that general LLM capability does not automatically create trading capability; most tested agents had poor returns and weak risk management [3].
- StockBench similarly finds that most models struggle to beat a buy-and-hold baseline [4].
- A 2026 audit found only 2 of 19 closed-loop agent studies had extractable time-consistent splits, only one modeled transaction costs, and none achieved the highest reproducibility tier [5].

**Implication:** LLMs should be constrained research/orchestration components, not unconstrained predictive and execution engines.

### Predictive models

For cross-sectional return prediction, trees and neural networks have historically produced large gains over linear benchmarks by modeling nonlinear predictor interactions. Momentum, liquidity, and volatility consistently appear as important signals [6].

Relevant recent architectures:

- **StockMixer** is a strong, efficient MLP for mixing indicators, time horizons, and cross-stock relationships [7].
- **MASTER** is a market-guided transformer that models dynamic, cross-time stock correlations and adapts feature relevance to the market state [8].

For a hackathon, start with LightGBM or XGBoost plus well-defined features. They are quick to train, simple to explain, and robust enough to be a credible benchmark. Treat StockMixer/MASTER as optional challengers rather than mandatory infrastructure.

### News and event extraction

LLM-derived financial sentiment can improve return prediction. A study using 965,375 US financial news articles reported a self-financing strategy with an OPT-based sentiment signal that substantially outperformed a dictionary baseline [9]. However, historical LLM evaluations can be contaminated by model knowledge and company-identity effects; anonymizing named companies is one proposed mitigation [10].

Use an LLM to emit structured event records, not a vague sentiment label:

```json
{
  "event_type": "earnings_guidance",
  "direction": "negative",
  "magnitude": 0.81,
  "novelty": 0.74,
  "surprise": 0.67,
  "expected_horizon_minutes": 180,
  "confidence": 0.79,
  "affected_symbols": ["XYZ"],
  "invalidating_evidence": ["guidance withdrawal clarified"]
}
```

### Intraday structure

Both continuation and reversal effects exist intraday. Cross-sectional returns can continue at recurring half-hour intervals, while short-horizon reversal is associated with temporary liquidity imbalances and bid-ask bounce [11].

This supports Catalyst Router's key hypothesis: the same large move should be treated as continuation when it contains new information and reversion when it appears to be temporary liquidity pressure.

### Reinforcement learning

FinRL and FinRL-Meta offer useful environments, constraints, and reference implementations [12]. But a robustness benchmark found that many standard deep-RL portfolio methods generalized poorly and degraded quickly in backtests [13].

Do not use end-to-end PPO/SAC/DDPG as the sole production strategy in a short contest. A contextual bandit that allocates risk among independently validated strategies is more realistic. A trained RL model can run in shadow mode as a creative comparison or future extension.

## Proposed System: Catalyst Router

### Data and universe selection

Use Alpaca to identify the active, liquid universe on each trading day:

1. Query the most active stocks and market movers.
2. Filter to tradable, shortable, liquid equities.
3. Exclude low-priced names, halts, LULD events, large spreads, stale data, and recent corporate actions.
4. Select approximately 30-75 symbols rather than attempting to trade the complete market.
5. Include SPY, QQQ, sector ETFs, and optionally BTC/USD and ETH/USD as regime/risk-appetite references.

Initial filters:

- Price greater than `$5`.
- Median daily dollar volume greater than `$50M`.
- Spread below `8-15 bps`.
- No trading halt, LULD, or stale quote.
- Sufficient history and recent news coverage.

### Expert signals

| Expert | Input | Output |
| --- | --- | --- |
| Catalyst analyst | Alpaca news + LLM | Event type, direction, novelty, confidence, horizon |
| Momentum expert | Residual return, opening range, VWAP, volume | Continuation confidence |
| Reversion expert | Return z-score, VWAP distance, spread, liquidity | Reversion confidence |
| Regime expert | SPY/QQQ trend, breadth, realized volatility, correlation | Risk-on/risk-off/volatile regime |
| Risk critic | Exposure, drawdown, stale data, disagreement | Approve, reduce, veto |

The router must return one typed state:

```text
CATALYST_CONTINUATION
LIQUIDITY_REVERSION
REGIME_TREND
NO_TRADE
```

### Entry rules

**Catalyst continuation**

- Material news is timestamped before the move.
- Event confidence and novelty exceed calibrated thresholds.
- Price is above/below VWAP in the event direction.
- Relative volume and market-relative return confirm the thesis.
- Spread and estimated execution costs are acceptable.

**Liquidity reversion**

- No credible new information explains the move.
- Sector- and market-adjusted return is extreme.
- Price is materially displaced from VWAP.
- Volume does not indicate sustained information arrival.
- Avoid the first opening minutes and scheduled earnings windows.

Only enter when conservative edge clears costs:

```text
expected_edge > spread + estimated_slippage + uncertainty_buffer
```

### Risk and sizing

The LLM may propose a thesis but must never choose the quantity. Position size must derive from stop distance and an account-level risk budget:

```text
shares = per_trade_risk_budget / abs(entry_price - stop_price)
```

Initial hard limits:

- Risk per trade: `0.25%-0.50%` of equity.
- Maximum simultaneous positions: `5`.
- Maximum individual symbol exposure: `15%`.
- Maximum sector exposure: `30%`.
- Daily loss circuit breaker: `1.5%-2%`.
- Maximum portfolio drawdown: `4%-5%`.
- Do not average down unless a separately validated strategy explicitly requires it.

Use a small regime-conditioned SPY/QQQ sleeve only if the judging period is long enough to make being entirely in cash an unacceptable benchmark risk. Retain most capital for high-conviction active trades.

### Exits

- Use volatility-scaled profit targets and stops.
- Attach Alpaca bracket orders in regular market hours.
- Apply a maximum holding period consistent with the predicted event horizon.
- Exit on thesis invalidation as well as price stops.
- Close most intraday strategies before the bell.
- Hold overnight only for high-confidence multi-hour catalysts.

## Alpaca Implementation Plan

### Architecture

```text
Alpaca streams + news
        |
Feature store -> quantitative experts -> router -> LLM event analyst
        |                                         |
        +----------------> Risk Governor <--------+
                                 |
                          TradeIntent validator
                                 |
                  Alpaca Trading API / MCP / CLI
                                 |
                   Trade-update stream + audit log
```

### Trading API and alpaca-py

Use the Python SDK for the always-on service and WebSocket subscriptions:

- Real-time quotes, trades, minute bars, updated bars, statuses, and LULDs.
- Historical bars and snapshots for feature construction.
- Trading stream for fill, cancellation, rejection, and replacement updates.
- Market clock/calendar to avoid invalid session behavior.
- Account, positions, and activity APIs for reconciliation.

Alpaca recommends streaming for current data rather than polling historical endpoints. Updated bars can arrive after their original minute, so the feature pipeline must handle bar corrections rather than treating the first received value as final.

### MCP server

Use MCP as the LLM-facing tool interface. Useful tools include:

- `get_market_movers`, `get_most_active_stocks`, and `get_stock_snapshot`.
- `get_stock_bars`, `get_stock_quotes`, and `get_news`.
- `get_account_info`, `get_all_positions`, and `get_orders`.
- `place_stock_order`, `cancel_order_by_id`, and `replace_order_by_id`.

The LLM should produce a typed `TradeIntent`; the deterministic Risk Governor checks price freshness, buying power, signal confidence, stop placement, exposure, duplicate order IDs, and kill-switch status before invoking an order tool.

### CLI

Use the Alpaca CLI as an operator and demonstration surface:

```bash
alpaca doctor
alpaca account get
alpaca position list
alpaca account portfolio
alpaca order submit --symbol AAPL --side buy --qty 1 --type market --dry-run
```

It produces structured JSON, supports `--dry-run`, and can retrieve account, positions, portfolio history, orders, market data, news, and watchlists. Include it in the demo to satisfy the technology-implementation criterion visibly.

### Execution safeguards

- Assign deterministic `client_order_id` values for idempotency.
- Treat the trade-update stream as order-state authority.
- Reconcile local state with Alpaca on startup and after reconnects.
- Reject stale quotes, stale news, invalid prices, and duplicate intents.
- Cancel aged or contradictory entry orders.
- Maintain a global kill switch and an automatic daily-loss circuit breaker.
- Use regular-hours bracket/OCO orders where applicable.
- Use limit orders only in extended hours. Bracket orders are not supported there.

### Paper-trading caveats

Alpaca paper trading simulates fills from real-time quotes but does not account for market impact, information leakage, latency-driven slippage, queue position, price improvement, regulatory fees, or dividends. A paper-only account has IEX data, which is a small portion of total US market volume. Do not build a sub-minute quote-level strategy without the appropriate consolidated data subscription, and do not optimize around simulator fill artifacts.

## Validation Before the Judged Period

The Deflated Sharpe Ratio corrects for multiple testing and non-normality, addressing selection bias created when many strategy configurations are tested [14].

Validation checklist:

1. Use chronological walk-forward splits, never random cross-validation.
2. Purge overlapping label windows between train and test sets.
3. Construct news and the symbol universe point-in-time.
4. Include signal, inference, and order latency before execution.
5. Fill at bid/ask plus conservative slippage, not at bar close.
6. Include estimated transaction costs even though paper trading may omit them.
7. Freeze parameters before the judged window.
8. Compare with cash, SPY, equal-weight, basic momentum, and no-LLM baselines.
9. Report results by strategy, event type, regime, side, symbol, and holding period.
10. Run several random seeds for neural or RL models.

Track P&L, return, Sharpe/Sortino, maximum drawdown, profit factor, hit rate, average win/loss, turnover, estimated costs, concentration, beta, calibration by confidence bucket, and P&L attributed to each expert.

## Alternative Concepts

| Concept | P&L potential | Originality | Delivery risk |
| --- | --- | --- | --- |
| Catalyst Router | High | High | Medium |
| Regime-adaptive strategy council | High | Medium | Medium |
| Crypto-to-equity Night Watch | Medium-high | High | Medium |
| Event-driven options volatility agent | Medium | Very high | High |
| Supply-chain news propagation graph | Medium | Very high | High |
| Intraday seasonality/reversal agent | Medium | Medium | Low |

### Crypto-to-equity Night Watch

Use BTC/USD, ETH/USD, overnight equity data, news, and global-risk proxies to estimate risk appetite ahead of the US open. Trade liquid index ETFs and semiconductors rather than trying to predict individual names. It is visually compelling because the agent is active while US cash equities are closed.

### Event-driven options volatility agent

Compare structured LLM uncertainty about scheduled events with observed implied volatility, then trade defined-risk spreads. This is original, but data, fill behavior, and short contest windows make P&L less dependable.

### Supply-chain propagation graph

Build an explicit graph of suppliers, customers, competitors, sectors, and ETFs. When news hits one company, estimate delayed effects on related names. This is differentiated, but requires point-in-time relationship data and careful claim validation.

### Regime-adaptive strategy council

Let several small deterministic strategies vote: trend, mean reversion, event continuation, defensive ETF rotation, and cash. A contextual bandit allocates a risk budget to strategies with current evidence, rather than asking a single model to solve every regime.

## Presentation Plan

Central message:

> We do not ask an LLM to guess the next price. We ask it whether a move contains new information, then route the move to a validated quantitative strategy under a hard risk governor.

Demonstrate these artifacts live:

1. Current Alpaca movers and selected liquid universe.
2. A generated structured event card from Alpaca news.
3. Bull, bear, and risk-critic viewpoints.
4. A visible veto or `NO_TRADE` example, not only successful trades.
5. The typed `TradeIntent` and deterministic risk checks.
6. MCP tool calls and CLI `--dry-run`/account inspection.
7. Submission, trade update, bracket creation, and exit lifecycle.
8. P&L and drawdown against SPY and simple baselines.
9. An ablation without event routing or LLM extraction.
10. A complete, timestamped decision trail for every trade.

## References

[1] [TradingAgents: Multi-Agents LLM Financial Trading Framework](https://consensus.app/papers/details/e7ae4968482e5773b718765f4182ddaf/?utm_source=unknown), Xiao et al., 2024, arXiv. “The framework includes Bull and Bear researcher agents assessing market conditions, a risk management team monitoring exposure, and traders synthesizing insights.”

[2] [FinMem: A Performance-Enhanced LLM Trading Agent With Layered Memory and Character Design](https://consensus.app/papers/details/5651cdce424e54bea80bc7f0e5469351/?utm_source=unknown), Yu et al., 2023, IEEE Transactions on Big Data. “The Memory module... employs a layered approach to process and prioritize data based on its timeliness and relevance.”

[3] [AI-Trader: Benchmarking Autonomous Agents in Real-Time Financial Markets](https://consensus.app/papers/details/2a4518ff43dc5107841241801020596c/?utm_source=unknown), Fan et al., 2025, arXiv. “General intelligence does not automatically translate to effective trading capability, with most agents exhibiting poor returns and weak risk management.”

[4] [StockBench: Can LLM Agents Trade Stocks Profitably In Real-world Markets?](https://consensus.app/papers/details/f9feb25af9a6556baa4fadf0d32c6c31/?utm_source=unknown), Chen et al., 2025, arXiv. “Most models struggle to outperform the simple buy-and-hold baseline.”

[5] [Agentic Trading: When LLM Agents Meet Financial Markets](https://consensus.app/papers/details/33b2ad06c2cf5136bd3a5109bf6fa769/?utm_source=unknown), Xia et al., 2026, arXiv. “Only 2/19 studies report extractable time-consistent split protocols... and no study reaches R3 reproducibility.”

[6] Gu, Kelly, and Xiu. [Empirical Asset Pricing via Machine Learning](https://doi.org/10.1093/rfs/hhaa009), 2020. DOI: `10.1093/rfs/hhaa009`. “We identify the best-performing methods (trees and neural networks)... momentum, liquidity, and volatility.”

[7] Fan and Shen. [StockMixer: A Simple Yet Strong MLP-Based Architecture for Stock Price Forecasting](https://doi.org/10.1609/aaai.v38i8.28681), 2024. DOI: `10.1609/aaai.v38i8.28681`. “StockMixer outperforms various state-of-the-art forecasting methods with a notable margin while reducing memory usage and runtime cost.”

[8] Li et al. [MASTER: Market-Guided Stock Transformer for Stock Price Forecasting](https://doi.org/10.1609/aaai.v38i1.27767), 2024. DOI: `10.1609/aaai.v38i1.27767`. “MASTER... models the momentary and cross-time stock correlation and leverages market information for automatic feature selection.”

[9] Kirtac and Germano. [Enhanced Financial Sentiment Analysis and Trading Strategy Development Using Large Language Models](https://doi.org/10.18653/v1/2024.wassa-1.1), 2024. DOI: `10.18653/v1/2024.wassa-1.1`. “A self-financing strategy based on OPT scores achieves a Sharpe ratio of 3.05.”

[10] Glasserman and Lin. [Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis](https://arxiv.org/abs/2309.17322), 2023. DOI: `10.48550/arXiv.2309.17322`. “The authors’ proposed anonymization procedure is therefore potentially useful in out-of-sample implementation.”

[11] Heston, Korajczyk, and Sadka. [Intraday Patterns in the Cross-Section of Stock Returns](https://doi.org/10.1111/j.1540-6261.2010.01573.x), 2010. DOI: `10.1111/j.1540-6261.2010.01573.x`. “Short-term return reversal is driven by temporary liquidity imbalances lasting less than an hour and bid-ask bounce.”

[12] Liu et al. [FinRL-Meta: Market Environments and Benchmarks for Data-Driven Financial Reinforcement Learning](https://doi.org/10.52202/068431-0134), 2022. DOI: `10.52202/068431-0134`. “Low signal-to-noise ratio... survivorship bias... and model overfitting” are central challenges.

[13] Velay et al. [Benchmarking Robustness of Deep Reinforcement Learning Approaches to Online Portfolio Management](https://doi.org/10.1109/INISTA59065.2023.10310402), 2023. DOI: `10.1109/INISTA59065.2023.10310402`. “Most Deep Reinforcement Learning algorithms were not robust, with strategies generalizing poorly.”

[14] Bailey and Lopez de Prado. [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality](https://doi.org/10.3905/jpm.2014.40.5.094), 2014. DOI: `10.3905/jpm.2014.40.5.094`. “DSR helps separate legitimate empirical findings from statistical flukes.”

## Official Alpaca Documentation

- [Trading MCP Server](https://docs.alpaca.markets/us/docs/alpaca-mcp-server)
- [Trading CLI](https://docs.alpaca.markets/us/docs/alpacas-cli)
- [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading)
- [Placing Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)
- [Real-time Stock Data](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data)
- [Historical Stock Data](https://docs.alpaca.markets/us/docs/historical-stock-data-1)
