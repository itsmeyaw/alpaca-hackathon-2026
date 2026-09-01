# Local Challenger Benchmark

## Status

The selected local price-only challenger is `xgboost_regularized` using feature schema
`bar-features-v2` and a four-hour horizon. Its artifact records a `0.60` shadow gate. ADR-0013
authorizes it for Alpaca paper execution with a more aggressive `0.52` runtime gate while preserving
the validation limitations below.

This benchmark remains historical evidence for the deployed 15-minute artifact. ADR-0014 introduces
the five-minute `bar-features-v3` pipeline in shadow mode; it requires a new benchmark and must not
reuse the results below as promotion evidence.

## Five-Minute Shadow Run

Run `20260829T092122Z` trained on 2,157,716 five-minute bars for 20 symbols from January 2021
through August 29, 2026. After rejecting missing-bar and cross-session feature windows and requiring
same-session four-hour labels, the sample contained 131,294 examples: 107,385 development and
23,841 diagnostic holdout examples.

The best runtime-compatible candidate was `xgboost_return_shallow` at an 8 basis-point edge gate.
Its four validation folds produced mean return `-3.57%`, mean Sharpe `-0.36`, mean maximum drawdown
`14.64%`, and selection score `-1.46`; only one fold had positive cumulative return. The diagnostic
holdout returned `-17.21%` with Sharpe `-4.01`, maximum drawdown `17.54%`, and 75 trades.

The artifact remains `SHADOW_ONLY`, `promotion_eligible=false`, and failed its numeric shadow gate.
It must not replace or inherit authority from the ADR-0013 model.

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

The next valid test is forward collection with quotes, Alpaca News, incumbent decisions, and
realized executable prices. ADR-0013 records the operator's explicit paper-live override; it does not
claim the original promotion criteria were met.
