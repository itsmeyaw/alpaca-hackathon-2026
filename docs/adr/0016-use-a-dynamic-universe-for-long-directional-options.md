# ADR-0016: Use a Dynamic Universe for Long Directional Options

- Status: Accepted
- Date: 2026-09-03

## Context

The paper-live classifier was trained and validated on a fixed panel and ADR-0013 authorized only
long and short equity intents. Runtime continued to fetch the artifact manifest's symbols even though
ADR-0012 requires a daily universe of liquid option-enabled movers and active stocks. The classifier
does not encode symbol identity, but applying it to newly selected symbols is still an explicit
distribution shift.

The competition calls for an options implementation. Long calls and puts bound maximum loss to the
premium, while short option legs and 0DTE contracts remain prohibited. Alpaca Basic supplies
indicative option quotes and Greeks, so incomplete or illiquid chains must fail closed.

## Decision

Build one immutable universe per Alpaca trading session from a reciprocal-rank union of the 100 most
active stocks, 50 gainers, and 50 losers. Select up to 20 active, tradable, option-enabled equities
with price at least `$5`, prior-day dollar volume at least `$50M`, and IEX spread at most `15 bps`.
Always retain SPY as market context, but trade it only when it is selected. Persist the universe so a
worker restart cannot change the day's opportunity set.

When explicitly enabled, replace model directional equity entries with long options: bullish signals
buy calls and bearish signals buy puts. Require 14-30 DTE, `0.55-0.70` absolute delta, a standard 100
multiplier, a fresh complete indicative quote, spread at most 10%, open interest at least 100, and
positive bid and ask sizes. Rank eligible contracts deterministically.

Budget the entire premium as maximum loss. Actively close at a 30% premium loss, 50% premium gain,
the four-hour model horizon, or the session cutoff. A managed position pauses new entries on an
unavailable or stale quote, resumes supervision when quote quality recovers, and risk-halts and
flattens only after 60 seconds of continuous quote failure. Continue to enforce account option level,
option buying power, position count, group and total risk, reconciliation, idempotent entry claims,
daily loss, and competition drawdown. The feature artifact is unchanged; no retraining or alteration
of its recorded validation evidence is implied.

Production activation requires `MODEL_OPTIONS_EXECUTION_ENABLED=true` in addition to the existing
paper-live model gates. The flag defaults off in configuration and infrastructure.

## Consequences

- Runtime opportunities follow current market activity instead of the historical training panel.
- Historical validation does not estimate dynamic-universe or option-premium returns.
- Full-premium budgeting remains valid if managed exits fail or gap through their trigger.
- Indicative-data and liquidity gates will reject many directional signals.
- Long options require active process supervision because equity bracket behavior is not assumed.
