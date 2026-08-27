# Local Challenger Benchmark

## Status

The selected local price-only challenger is `xgboost_regularized` using feature schema
`bar-features-v2`, a four-hour horizon, and a `0.60` directional probability gate. It is
`SHADOW_ONLY` and has no path to `TradeIntent`, risk approval, or order execution.

## Protocol

- Source: raw Alpaca IEX 15-minute regular-session bars.
- Sample: 12 fixed liquid symbols from January 2025 through August 27, 2026.
- Features: lagged returns, range, VWAP distance, volume, volatility, SPY context,
  cross-sectional rank, range location, and time of day. Every feature is available at bar close.
- Validation: four expanding chronological folds with a label-horizon purge.
- Diagnostic holdout: final 15% of timestamps beginning June 1, 2026.
- Simulation: at most three equal-weight long or short positions per non-overlapping rebalance.
- Cost: 12 basis points round trip per selected position.
- Selection score: mean fold Sharpe minus fold-Sharpe dispersion and five times mean drawdown.

## Iterations

| Iteration | Best approach | Horizon | Mean validation return | Diagnostic holdout |
| --- | --- | ---: | ---: | ---: |
| Direction classification, v1 features | Logistic regression | 1 hour | -27.46% | -29.91% |
| Direct return regression, v1 features | Ridge regression | 1 hour | -3.36% | -2.58% |
| Horizon search, v1 features | Robust XGBoost return | 3.25 hours | +0.91% | +12.12% |
| Horizon search, v1 features | Ridge regression | 2 hours | -3.95% | -7.93% |
| Horizon search, v1 features | Robust XGBoost return | 4 hours | +2.78% | +8.78% |
| Contextual v2 features | Regularized XGBoost classifier | 4 hours | +1.60% | +8.38% |

The final selected configuration was positive in two of four validation folds:

| Fold | Return | Sharpe | Maximum drawdown | Trades |
| ---: | ---: | ---: | ---: | ---: |
| 1 | -0.13% | 0.04 | 7.33% | 110 |
| 2 | +1.54% | 0.76 | 2.99% | 98 |
| 3 | -1.18% | -0.41 | 8.18% | 90 |
| 4 | +6.19% | 2.05 | 5.96% | 96 |

Aggregate validation mean Sharpe was `0.61`; the conservative selection score was `-0.62`.
The diagnostic holdout returned `+8.38%`, with Sharpe `3.22`, maximum drawdown `3.94%`,
and 76 trades across 46 invested periods.

## Reproduce

```bash
make train-local
```

Use `--refresh` to replace the cached source bars. The ignored artifact manifest records the data
SHA-256, feature schema, package versions, model parameters, model hash, folds, and ranking.

## Limits

- The holdout was inspected during iterative horizon and feature work, so it is diagnostic rather
  than untouched promotion evidence.
- The fixed universe avoids dynamic selection leakage but does not model historical universe
  construction or delistings.
- Bar data cannot reproduce executable bid/ask prices, halts, borrow availability, or market impact.
- Simulated returns are equal-weight basket returns, not expected account P&L under the Risk Governor.
- The sample has no point-in-time news/event features and cannot compare the full catalyst incumbent.
- Fold instability and 76 diagnostic holdout trades are insufficient for promotion confidence.

The next valid test is forward shadow collection with quotes, Alpaca News, incumbent decisions,
and realized executable prices. Promotion remains a separate versioned decision under ADR-0005.
