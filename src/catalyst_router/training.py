from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from math import cos, isfinite, pi, sin, sqrt
from pathlib import Path
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo

FEATURE_NAMES = (
    "return_1",
    "return_4",
    "return_12",
    "bar_range",
    "vwap_distance",
    "volume_ratio",
    "realized_vol_8",
    "relative_return_4",
    "relative_return_12",
    "market_return_4",
    "market_return_12",
    "market_realized_vol_8",
    "close_location_20",
    "cross_sectional_return_rank_4",
    "minute_sin",
    "minute_cos",
)

_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("market bar timestamps must be timezone-aware")
        if min(self.open, self.high, self.low, self.close, self.vwap) <= 0:
            raise ValueError("market bar prices must be positive")
        if self.volume < 0:
            raise ValueError("market bar volume must be nonnegative")


@dataclass(frozen=True, slots=True)
class TrainingExample:
    symbol: str
    observed_at: datetime
    label_end_at: datetime
    features: tuple[float, ...]
    forward_return: float

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.label_end_at.tzinfo is None:
            raise ValueError("training example timestamps must be timezone-aware")
        if self.label_end_at <= self.observed_at:
            raise ValueError("label end must be after the observation")
        if len(self.features) != len(FEATURE_NAMES):
            raise ValueError("training example does not match the feature schema")
        if not all(isfinite(value) for value in (*self.features, self.forward_return)):
            raise ValueError("training examples must contain finite values")


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    trades: int
    periods: int
    hit_rate: float
    mean_period_return: float
    cumulative_return: float
    annualized_sharpe: float
    max_drawdown: float


@dataclass(frozen=True, slots=True)
class BarCacheSpecification:
    start: str
    end: str
    symbols: tuple[str, ...]
    timeframe_minutes: int


def validate_bar_cache(path: Path, specification: BarCacheSpecification) -> None:
    metadata_path = path.with_suffix(".meta.json")
    if not metadata_path.exists():
        raise RuntimeError("bar cache metadata is missing; rerun with --refresh")
    metadata: Mapping[str, object] = json.loads(metadata_path.read_text())
    expected: Mapping[str, object] = {
        "adjustment": "raw",
        "end": specification.end,
        "feed": "iex",
        "start": specification.start,
        "symbols": list(specification.symbols),
        "timeframe_minutes": specification.timeframe_minutes,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise RuntimeError(f"bar cache metadata mismatch for {key}; rerun with --refresh")
    if metadata.get("sha256") != sha256_file(path):
        raise RuntimeError("bar cache checksum mismatch; rerun with --refresh")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _PartialExample:
    symbol: str
    observed_at: datetime
    label_end_at: datetime
    features: tuple[float, ...]
    return_4: float
    return_12: float
    realized_vol_8: float
    forward_return: float


def build_training_examples(
    bars: list[MarketBar],
    *,
    horizon_bars: int = 4,
    min_history: int = 20,
    market_symbol: str = "SPY",
) -> tuple[TrainingExample, ...]:
    """Build close-of-bar features whose labels remain in the same trading session."""
    if horizon_bars < 1:
        raise ValueError("horizon_bars must be positive")
    if min_history < 13:
        raise ValueError("min_history must be at least 13 bars")

    by_symbol: dict[str, list[MarketBar]] = defaultdict(list)
    for bar in bars:
        by_symbol[bar.symbol].append(bar)

    partials: list[_PartialExample] = []
    for symbol, symbol_bars in by_symbol.items():
        ordered = sorted(symbol_bars, key=lambda item: item.timestamp)
        if len({bar.timestamp for bar in ordered}) != len(ordered):
            raise ValueError(f"duplicate timestamps for {symbol}")

        one_bar_returns = [0.0]
        one_bar_returns.extend(
            current.close / previous.close - 1 for previous, current in pairwise(ordered)
        )
        for index in range(min_history - 1, len(ordered) - horizon_bars):
            current = ordered[index]
            future = ordered[index + horizon_bars]
            if _session_date(current.timestamp) != _session_date(future.timestamp):
                continue

            return_1 = current.close / ordered[index - 1].close - 1
            return_4 = current.close / ordered[index - 4].close - 1
            return_12 = current.close / ordered[index - 12].close - 1
            volume_window = ordered[index - min_history + 1 : index + 1]
            mean_volume = fmean(bar.volume for bar in volume_window)
            window_low = min(bar.low for bar in volume_window)
            window_high = max(bar.high for bar in volume_window)
            window_span = window_high - window_low
            realized_volatility = sqrt(
                fmean(value * value for value in one_bar_returns[index - 7 : index + 1])
            )
            local_timestamp = current.timestamp.astimezone(_NEW_YORK)
            minute = local_timestamp.hour * 60 + local_timestamp.minute
            angle = 2 * pi * (minute - 570) / 390
            partials.append(
                _PartialExample(
                    symbol=symbol,
                    observed_at=current.timestamp,
                    label_end_at=future.timestamp,
                    features=(
                        return_1,
                        return_4,
                        return_12,
                        (current.high - current.low) / current.close,
                        current.close / current.vwap - 1,
                        current.volume / mean_volume - 1 if mean_volume else 0.0,
                        realized_volatility,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        (current.close - window_low) / window_span - 0.5 if window_span else 0.0,
                        0.0,
                        sin(angle),
                        cos(angle),
                    ),
                    return_4=return_4,
                    return_12=return_12,
                    realized_vol_8=realized_volatility,
                    forward_return=future.close / current.close - 1,
                )
            )

    market_context = {
        item.observed_at: (item.return_4, item.return_12, item.realized_vol_8)
        for item in partials
        if item.symbol == market_symbol
    }
    by_timestamp: dict[datetime, list[_PartialExample]] = defaultdict(list)
    for item in partials:
        by_timestamp[item.observed_at].append(item)
    cross_sectional_rank = {}
    for timestamp, items in by_timestamp.items():
        ranked_items = sorted(items, key=lambda item: item.return_4)
        denominator = max(1, len(ranked_items) - 1)
        for rank, item in enumerate(ranked_items):
            cross_sectional_rank[(timestamp, item.symbol)] = rank / denominator - 0.5

    examples = []
    for item in partials:
        context = market_context.get(item.observed_at)
        if context is None:
            continue
        market_return_4, market_return_12, market_volatility = context
        features = list(item.features)
        features[7] = item.return_4 - market_return_4
        features[8] = item.return_12 - market_return_12
        features[9] = market_return_4
        features[10] = market_return_12
        features[11] = market_volatility
        features[13] = cross_sectional_rank[(item.observed_at, item.symbol)]
        examples.append(
            TrainingExample(
                symbol=item.symbol,
                observed_at=item.observed_at,
                label_end_at=item.label_end_at,
                features=tuple(features),
                forward_return=item.forward_return,
            )
        )
    return tuple(sorted(examples, key=lambda item: (item.observed_at, item.symbol)))


def purged_walk_forward_splits(
    examples: tuple[TrainingExample, ...],
    *,
    folds: int,
    minimum_train_timestamps: int,
    purge: timedelta,
) -> tuple[WalkForwardFold, ...]:
    """Create expanding chronological folds without splitting or overlapping timestamps."""
    if folds < 1:
        raise ValueError("folds must be positive")
    if minimum_train_timestamps < 1:
        raise ValueError("minimum_train_timestamps must be positive")
    if purge < timedelta(0):
        raise ValueError("purge must be nonnegative")

    timestamps = sorted({item.observed_at for item in examples})
    remaining = len(timestamps) - minimum_train_timestamps
    if remaining < folds:
        raise ValueError("not enough timestamps for requested walk-forward folds")
    validation_size = remaining // folds

    result = []
    for fold in range(folds):
        validation_start_index = minimum_train_timestamps + fold * validation_size
        validation_end_index = (
            len(timestamps) if fold == folds - 1 else validation_start_index + validation_size
        )
        validation_timestamps = set(timestamps[validation_start_index:validation_end_index])
        validation_start = timestamps[validation_start_index]
        train_indices = tuple(
            index
            for index, item in enumerate(examples)
            if max(item.label_end_at, item.observed_at + purge) < validation_start
        )
        validation_indices = tuple(
            index
            for index, item in enumerate(examples)
            if item.observed_at in validation_timestamps
        )
        if not train_indices or not validation_indices:
            raise ValueError("walk-forward fold is empty after purging")
        result.append(WalkForwardFold(train_indices, validation_indices))
    return tuple(result)


def evaluate_predictions(
    examples: tuple[TrainingExample, ...],
    probabilities: tuple[float, ...],
    *,
    confidence_threshold: float,
    cost_bps: float,
    max_positions: int,
    periods_per_year: float,
) -> EvaluationResult:
    """Evaluate ranked long/short predictions as non-overlapping, equal-risk baskets."""
    if len(examples) != len(probabilities):
        raise ValueError("each example requires one prediction")
    if not 0.5 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between 0.5 and 1")
    if cost_bps < 0 or max_positions < 1 or periods_per_year <= 0:
        raise ValueError("evaluation parameters must be positive")
    if not all(isfinite(value) and 0 <= value <= 1 for value in probabilities):
        raise ValueError("predictions must be finite probabilities")
    scores = tuple(value - 0.5 for value in probabilities)
    return _evaluate_ranked_scores(
        examples,
        scores,
        minimum_score=confidence_threshold - 0.5,
        cost_bps=cost_bps,
        max_positions=max_positions,
        periods_per_year=periods_per_year,
    )


def evaluate_return_forecasts(
    examples: tuple[TrainingExample, ...],
    forecasts: tuple[float, ...],
    *,
    minimum_edge_bps: float,
    cost_bps: float,
    max_positions: int,
    periods_per_year: float,
) -> EvaluationResult:
    """Evaluate only return forecasts whose expected edge clears costs and an edge buffer."""
    if len(examples) != len(forecasts):
        raise ValueError("each example requires one forecast")
    if minimum_edge_bps < 0:
        raise ValueError("minimum_edge_bps must be nonnegative")
    if not all(isfinite(value) for value in forecasts):
        raise ValueError("forecasts must be finite")
    return _evaluate_ranked_scores(
        examples,
        forecasts,
        minimum_score=(cost_bps + minimum_edge_bps) / 10_000,
        cost_bps=cost_bps,
        max_positions=max_positions,
        periods_per_year=periods_per_year,
    )


def _evaluate_ranked_scores(
    examples: tuple[TrainingExample, ...],
    scores: tuple[float, ...],
    *,
    minimum_score: float,
    cost_bps: float,
    max_positions: int,
    periods_per_year: float,
) -> EvaluationResult:
    if cost_bps < 0 or max_positions < 1 or periods_per_year <= 0:
        raise ValueError("evaluation parameters must be positive")

    by_timestamp: dict[datetime, list[tuple[TrainingExample, float]]] = defaultdict(list)
    for item, score in zip(examples, scores, strict=True):
        by_timestamp[item.observed_at].append((item, score))

    next_available: dict[object, datetime] = {}
    period_returns = []
    trade_returns = []
    cost = cost_bps / 10_000
    for timestamp in sorted(by_timestamp):
        session = _session_date(timestamp)
        if timestamp < next_available.get(session, timestamp):
            continue
        ranked = sorted(by_timestamp[timestamp], key=lambda pair: abs(pair[1]), reverse=True)
        selected = [pair for pair in ranked if abs(pair[1]) >= minimum_score and pair[1] != 0][
            :max_positions
        ]
        if not selected:
            continue
        returns = [
            (1 if score > 0 else -1) * item.forward_return - cost for item, score in selected
        ]
        trade_returns.extend(returns)
        period_returns.append(fmean(returns))
        next_available[session] = max(item.label_end_at for item, _ in selected)

    if not period_returns:
        return EvaluationResult(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    equity = 1.0
    peak = equity
    max_drawdown = 0.0
    for period_return in period_returns:
        equity *= 1 + period_return
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    volatility = pstdev(period_returns)
    sharpe = fmean(period_returns) / volatility * sqrt(periods_per_year) if volatility else 0.0
    return EvaluationResult(
        trades=len(trade_returns),
        periods=len(period_returns),
        hit_rate=sum(value > 0 for value in trade_returns) / len(trade_returns),
        mean_period_return=fmean(period_returns),
        cumulative_return=equity - 1,
        annualized_sharpe=sharpe,
        max_drawdown=max_drawdown,
    )


def _session_date(timestamp: datetime) -> object:
    return timestamp.astimezone(_NEW_YORK).date()
