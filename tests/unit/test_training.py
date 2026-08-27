import json
from datetime import UTC, datetime, timedelta
from math import isclose
from pathlib import Path

import pytest

from catalyst_router.training import (
    FEATURE_NAMES,
    BarCacheSpecification,
    MarketBar,
    TrainingExample,
    build_training_examples,
    evaluate_predictions,
    evaluate_return_forecasts,
    purged_walk_forward_splits,
    sha256_file,
    validate_bar_cache,
)


def bars(symbol: str, closes: list[float]) -> list[MarketBar]:
    start = datetime(2026, 8, 24, 13, 30, tzinfo=UTC)
    return [
        MarketBar(
            symbol=symbol,
            timestamp=start + timedelta(minutes=15 * index),
            open=close - 0.1,
            high=close + 0.2,
            low=close - 0.2,
            close=close,
            volume=1_000 + index * 10,
            vwap=close - 0.05,
        )
        for index, close in enumerate(closes)
    ]


def example(
    symbol: str,
    observed_at: datetime,
    forward_return: float,
    *,
    label_horizon: timedelta = timedelta(hours=1),
) -> TrainingExample:
    return TrainingExample(
        symbol=symbol,
        observed_at=observed_at,
        label_end_at=observed_at + label_horizon,
        features=(0.0,) * len(FEATURE_NAMES),
        forward_return=forward_return,
    )


def test_builds_point_in_time_features_and_same_session_label() -> None:
    spy = bars("SPY", [100.0 + index for index in range(25)])
    aapl = bars("AAPL", [200.0 + 2 * index for index in range(25)])

    examples = build_training_examples(spy + aapl, horizon_bars=2, min_history=20)

    first_aapl = next(item for item in examples if item.symbol == "AAPL")
    feature = dict(zip(FEATURE_NAMES, first_aapl.features, strict=True))
    assert first_aapl.observed_at == aapl[19].timestamp
    assert isclose(feature["return_1"], 2 / 236)
    assert isclose(feature["relative_return_4"], (238 / 230 - 1) - (119 / 115 - 1))
    assert isclose(feature["market_return_4"], 119 / 115 - 1)
    assert isclose(feature["relative_return_12"], (238 / 214 - 1) - (119 / 107 - 1))
    assert isclose(first_aapl.forward_return, 242 / 238 - 1)


def test_walk_forward_splits_purge_overlapping_training_labels() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    examples = tuple(
        example(
            "SPY",
            start + timedelta(hours=index),
            0.01,
            label_horizon=timedelta(hours=2),
        )
        for index in range(20)
    )

    folds = purged_walk_forward_splits(
        examples,
        folds=2,
        minimum_train_timestamps=8,
        purge=timedelta(hours=2),
    )

    first = folds[0]
    validation_start = examples[first.validation_indices[0]].observed_at
    assert all(examples[index].label_end_at < validation_start for index in first.train_indices)
    assert all(
        examples[index].observed_at >= validation_start for index in first.validation_indices
    )


def test_walk_forward_uses_actual_label_end_when_bars_are_missing() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    examples = tuple(
        example(
            "SPY",
            start + timedelta(hours=index),
            0.01,
            label_horizon=timedelta(hours=5 if index == 5 else 1),
        )
        for index in range(20)
    )

    first = purged_walk_forward_splits(
        examples,
        folds=2,
        minimum_train_timestamps=8,
        purge=timedelta(hours=2),
    )[0]

    assert 5 not in first.train_indices


def test_evaluates_ranked_non_overlapping_predictions_after_costs() -> None:
    start = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
    examples = (
        example("AAPL", start, 0.020),
        example("MSFT", start, -0.010),
        example("NVDA", start, 0.030),
        example("AAPL", start + timedelta(hours=1), -0.020),
        example("MSFT", start + timedelta(hours=1), 0.010),
        example("NVDA", start + timedelta(hours=1), -0.030),
    )
    probabilities = (0.90, 0.10, 0.60, 0.10, 0.90, 0.40)

    result = evaluate_predictions(
        examples,
        probabilities,
        confidence_threshold=0.60,
        cost_bps=10,
        max_positions=2,
        periods_per_year=1,
    )

    assert result.trades == 4
    assert result.periods == 2
    assert result.hit_rate == 1.0
    assert isclose(result.mean_period_return, 0.014)
    assert isclose(result.cumulative_return, (1.014 * 1.014) - 1)


def test_return_forecasts_abstain_until_predicted_edge_clears_costs() -> None:
    start = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
    examples = (
        example("AAPL", start, 0.020),
        example("MSFT", start, -0.010),
        example("NVDA", start, 0.030),
    )

    result = evaluate_return_forecasts(
        examples,
        forecasts=(0.0040, -0.0030, 0.0015),
        minimum_edge_bps=10,
        cost_bps=10,
        max_positions=3,
        periods_per_year=1,
    )

    assert result.trades == 2
    assert result.hit_rate == 1.0
    assert isclose(result.mean_period_return, 0.014)


def test_rejects_tampered_bar_cache(tmp_path: Path) -> None:
    cache = tmp_path / "bars.csv"
    cache.write_text("original")
    specification = BarCacheSpecification("2025-01-01", "2026-01-01", ("SPY",), 15)
    cache.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "adjustment": "raw",
                "end": specification.end,
                "feed": "iex",
                "sha256": sha256_file(cache),
                "start": specification.start,
                "symbols": list(specification.symbols),
                "timeframe_minutes": specification.timeframe_minutes,
            }
        )
    )
    cache.write_text("tampered")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        validate_bar_cache(cache, specification)
