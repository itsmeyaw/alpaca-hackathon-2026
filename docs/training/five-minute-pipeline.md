# Five-Minute Challenger Pipeline

## Contract

- Raw Alpaca IEX regular-session bars at five-minute resolution.
- `bar-features-v3` uses continuous intraday 15-minute, one-hour, and two-hour windows. Feature
  histories with missing bars or session boundaries are rejected rather than bridged.
- The prediction and Trade Intent horizon remains four hours (`48` bars).
- Classification training excludes returns that do not clear configured round-trip costs.
- Evaluation selects at most one position per rebalance to match the live runtime.
- Every artifact remains `SHADOW_ONLY` until a versioned promotion decision names its hashes.

## Data

`make train-local` maintains the source CSV cache for Alpaca download compatibility and creates an
immutable dataset below `.local/training/datasets/<cache-hash>/`. Bars are stored as compressed
Parquet partitions by symbol and year. `manifest.json` records partition row counts and SHA-256
digests; training records the dataset-manifest path and hash in the model manifest.

For a larger bounded panel, pass a comma-separated universe while retaining SPY as market context:

```bash
uv run --group training scripts/train-challengers \
  --start 2021-01-01 \
  --symbols SPY,QQQ,AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA,AMD,JPM,XOM,XLK,XLF,XLE
```

Use `--refresh` to replace the raw cache. A new cache checksum creates a new immutable dataset path.

## Promotion Evidence

Do not compare models by classification accuracy alone. Review after-cost return, fold consistency,
drawdown, turnover, trade count, and forward outcomes at executable quotes. The five-minute model
must outperform simple momentum/reversion baselines and the current 15-minute deployment before a
new ADR can grant paper authority.
