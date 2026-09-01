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

TIMEFRAME_MINUTES = 5
HORIZON_BARS = 48
FEATURE_HISTORY_BARS = 25
FEATURE_SCHEMA = "bar-features-v3"
FEATURE_SCHEMA_V2 = "bar-features-v2"
FEATURE_NAMES_V2 = (
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
FEATURE_NAMES = (
    "return_15m",
    "return_1h",
    "return_2h",
    "bar_range",
    "vwap_distance",
    "volume_ratio",
    "realized_vol_1h",
    "relative_return_1h",
    "relative_return_2h",
    "market_return_1h",
    "market_return_2h",
    "market_realized_vol_1h",
    "close_location_2h",
    "cross_sectional_return_rank_1h",
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
class FeatureVector:
    symbol: str
    observed_at: datetime
    schema: str
    names: tuple[str, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("feature vector timestamps must be timezone-aware")
        if len(self.names) != len(self.values):
            raise ValueError("feature names and values must have equal lengths")
        if not all(isfinite(value) for value in self.values):
            raise ValueError("feature vectors must contain finite values")


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
    return_1h: float
    return_2h: float
    realized_vol_1h: float
    forward_return: float


def economically_labeled_examples(
    examples: tuple[TrainingExample, ...], *, cost_bps: float
) -> tuple[tuple[TrainingExample, bool], ...]:
    """Return directional labels only where realized movement cleared round-trip costs."""
    if cost_bps < 0:
        raise ValueError("cost_bps must be nonnegative")
    minimum_return = cost_bps / 10_000
    return tuple(
        (item, item.forward_return > 0)
        for item in examples
        if abs(item.forward_return) > minimum_return
    )


def build_feature_vectors(
    bars: list[MarketBar],
    *,
    min_history: int | None = None,
    market_symbol: str = "SPY",
    feature_schema: str = FEATURE_SCHEMA,
) -> tuple[FeatureVector, ...]:
    """Build point-in-time features without requiring future labels."""
    if feature_schema == FEATURE_SCHEMA:
        names = FEATURE_NAMES
        return_short_bars, return_medium_bars, return_long_bars = 3, 12, 24
        volatility_bars, required_history = 12, FEATURE_HISTORY_BARS
        timeframe_minutes = 5
        allow_session_boundary = False
    elif feature_schema == FEATURE_SCHEMA_V2:
        names = FEATURE_NAMES_V2
        return_short_bars, return_medium_bars, return_long_bars = 1, 4, 12
        volatility_bars, required_history = 8, 20
        timeframe_minutes = 15
        allow_session_boundary = True
    else:
        raise ValueError(f"unsupported feature schema: {feature_schema}")
    min_history = min_history or required_history
    if min_history < required_history:
        raise ValueError(f"min_history must be at least {required_history} bars")

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
        for index in range(min_history - 1, len(ordered)):
            current = ordered[index]
            history = ordered[index - min_history + 1 : index + 1]
            if not _has_contiguous_intraday_bars(
                history,
                timeframe_minutes,
                allow_session_boundary=allow_session_boundary,
            ):
                continue
            return_15m = current.close / ordered[index - return_short_bars].close - 1
            return_1h = current.close / ordered[index - return_medium_bars].close - 1
            return_2h = current.close / ordered[index - return_long_bars].close - 1
            volume_window = ordered[index - min_history + 1 : index + 1]
            mean_volume = fmean(bar.volume for bar in volume_window)
            window_low = min(bar.low for bar in volume_window)
            window_high = max(bar.high for bar in volume_window)
            window_span = window_high - window_low
            realized_volatility = sqrt(
                fmean(
                    value * value
                    for value in one_bar_returns[index - volatility_bars + 1 : index + 1]
                )
            )
            local_timestamp = current.timestamp.astimezone(_NEW_YORK)
            minute = local_timestamp.hour * 60 + local_timestamp.minute
            angle = 2 * pi * (minute - 570) / 390
            partials.append(
                _PartialExample(
                    symbol=symbol,
                    observed_at=current.timestamp,
                    label_end_at=current.timestamp,
                    features=(
                        return_15m,
                        return_1h,
                        return_2h,
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
                    return_1h=return_1h,
                    return_2h=return_2h,
                    realized_vol_1h=realized_volatility,
                    forward_return=0.0,
                )
            )

    market_context = {
        item.observed_at: (item.return_1h, item.return_2h, item.realized_vol_1h)
        for item in partials
        if item.symbol == market_symbol
    }
    by_timestamp: dict[datetime, list[_PartialExample]] = defaultdict(list)
    for item in partials:
        by_timestamp[item.observed_at].append(item)
    cross_sectional_rank = {}
    for timestamp, items in by_timestamp.items():
        ranked_items = sorted(items, key=lambda item: item.return_1h)
        denominator = max(1, len(ranked_items) - 1)
        for rank, item in enumerate(ranked_items):
            cross_sectional_rank[(timestamp, item.symbol)] = rank / denominator - 0.5

    vectors = []
    for item in partials:
        context = market_context.get(item.observed_at)
        if context is None:
            continue
        market_return_1h, market_return_2h, market_volatility = context
        features = list(item.features)
        features[7] = item.return_1h - market_return_1h
        features[8] = item.return_2h - market_return_2h
        features[9] = market_return_1h
        features[10] = market_return_2h
        features[11] = market_volatility
        features[13] = cross_sectional_rank[(item.observed_at, item.symbol)]
        vectors.append(
            FeatureVector(
                symbol=item.symbol,
                observed_at=item.observed_at,
                schema=feature_schema,
                names=names,
                values=tuple(features),
            )
        )
    return tuple(sorted(vectors, key=lambda item: (item.observed_at, item.symbol)))


def build_training_examples(
    bars: list[MarketBar],
    *,
    horizon_bars: int = HORIZON_BARS,
    min_history: int = FEATURE_HISTORY_BARS,
    market_symbol: str = "SPY",
) -> tuple[TrainingExample, ...]:
    """Build close-of-bar features whose labels remain in the same trading session."""
    if horizon_bars < 1:
        raise ValueError("horizon_bars must be positive")
    if min_history < FEATURE_HISTORY_BARS:
        raise ValueError(f"min_history must be at least {FEATURE_HISTORY_BARS} bars")

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
            history = ordered[index - min_history + 1 : index + 1]
            if not _has_contiguous_intraday_bars(
                history, TIMEFRAME_MINUTES, allow_session_boundary=False
            ):
                continue
            if _session_date(current.timestamp) != _session_date(future.timestamp):
                continue
            if future.timestamp - current.timestamp != timedelta(
                minutes=TIMEFRAME_MINUTES * horizon_bars
            ):
                continue

            return_15m = current.close / ordered[index - 3].close - 1
            return_1h = current.close / ordered[index - 12].close - 1
            return_2h = current.close / ordered[index - 24].close - 1
            volume_window = ordered[index - min_history + 1 : index + 1]
            mean_volume = fmean(bar.volume for bar in volume_window)
            window_low = min(bar.low for bar in volume_window)
            window_high = max(bar.high for bar in volume_window)
            window_span = window_high - window_low
            realized_volatility = sqrt(
                fmean(value * value for value in one_bar_returns[index - 11 : index + 1])
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
                        return_15m,
                        return_1h,
                        return_2h,
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
                    return_1h=return_1h,
                    return_2h=return_2h,
                    realized_vol_1h=realized_volatility,
                    forward_return=future.close / current.close - 1,
                )
            )

    market_context = {
        item.observed_at: (item.return_1h, item.return_2h, item.realized_vol_1h)
        for item in partials
        if item.symbol == market_symbol
    }
    by_timestamp: dict[datetime, list[_PartialExample]] = defaultdict(list)
    for item in partials:
        by_timestamp[item.observed_at].append(item)
    cross_sectional_rank = {}
    for timestamp, items in by_timestamp.items():
        ranked_items = sorted(items, key=lambda item: item.return_1h)
        denominator = max(1, len(ranked_items) - 1)
        for rank, item in enumerate(ranked_items):
            cross_sectional_rank[(timestamp, item.symbol)] = rank / denominator - 0.5

    examples = []
    for item in partials:
        context = market_context.get(item.observed_at)
        if context is None:
            continue
        market_return_1h, market_return_2h, market_volatility = context
        features = list(item.features)
        features[7] = item.return_1h - market_return_1h
        features[8] = item.return_2h - market_return_2h
        features[9] = market_return_1h
        features[10] = market_return_2h
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


def _has_contiguous_intraday_bars(
    bars: list[MarketBar], timeframe_minutes: int, *, allow_session_boundary: bool
) -> bool:
    expected = timedelta(minutes=timeframe_minutes)
    return all(
        (
            allow_session_boundary
            and _session_date(previous.timestamp) != _session_date(current.timestamp)
        )
        or (
            _session_date(previous.timestamp) == _session_date(current.timestamp)
            and current.timestamp - previous.timestamp == expected
        )
        for previous, current in pairwise(bars)
    )
