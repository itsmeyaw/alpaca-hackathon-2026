import json
from datetime import UTC, datetime, timedelta
from math import isclose
from pathlib import Path

import pytest

from catalyst_router.training import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_V2,
    BarCacheSpecification,
    MarketBar,
    TrainingExample,
    build_feature_vectors,
    build_training_examples,
    economically_labeled_examples,
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
            timestamp=start + timedelta(minutes=5 * index),
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
    spy = bars("SPY", [100.0 + index for index in range(70)])
    aapl = bars("AAPL", [200.0 + 2 * index for index in range(70)])

    examples = build_training_examples(spy + aapl, horizon_bars=2)

    first_aapl = next(item for item in examples if item.symbol == "AAPL")
    feature = dict(zip(FEATURE_NAMES, first_aapl.features, strict=True))
    assert first_aapl.observed_at == aapl[24].timestamp
    assert isclose(feature["return_15m"], 248 / 242 - 1)
    assert isclose(feature["relative_return_1h"], (248 / 224 - 1) - (124 / 112 - 1))
    assert isclose(feature["market_return_1h"], 124 / 112 - 1)
    assert isclose(feature["relative_return_2h"], (248 / 200 - 1) - (124 / 100 - 1))
    assert isclose(first_aapl.forward_return, 252 / 248 - 1)


def test_live_feature_vector_matches_training_features_without_future_bars() -> None:
    spy = bars("SPY", [100.0 + index for index in range(25)])
    aapl = bars("AAPL", [200.0 + 2 * index for index in range(25)])
    future_spy = bars("SPY", [100.0 + index for index in range(27)])[25:]
    future_aapl = bars("AAPL", [200.0 + 2 * index for index in range(27)])[25:]

    live = build_feature_vectors(spy + aapl)
    trained = build_training_examples(
        spy + future_spy + aapl + future_aapl,
        horizon_bars=2,
    )

    live_aapl = next(item for item in live if item.symbol == "AAPL")
    trained_aapl = next(item for item in trained if item.symbol == "AAPL")
    assert live_aapl.schema == FEATURE_SCHEMA
    assert live_aapl.observed_at == trained_aapl.observed_at
    assert live_aapl.values == trained_aapl.features


def test_builds_legacy_feature_vectors_for_the_authorized_fifteen_minute_model() -> None:
    spy = bars("SPY", [100.0 + index for index in range(20)])
    aapl = bars("AAPL", [200.0 + index for index in range(20)])
    for symbol_bars in (spy, aapl):
        for index, bar in enumerate(symbol_bars):
            symbol_bars[index] = MarketBar(
                symbol=bar.symbol,
                timestamp=datetime(2026, 8, 24, 13, 30, tzinfo=UTC) + timedelta(minutes=15 * index),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                vwap=bar.vwap,
            )

    vectors = build_feature_vectors(spy + aapl, feature_schema=FEATURE_SCHEMA_V2)

    assert vectors
    assert {item.schema for item in vectors} == {FEATURE_SCHEMA_V2}
    assert {item.names for item in vectors} == {FEATURE_NAMES_V2}


def test_default_label_horizon_is_four_hours_on_five_minute_bars() -> None:
    spy = bars("SPY", [100.0 + index for index in range(120)])
    aapl = bars("AAPL", [200.0 + index for index in range(120)])

    first = build_training_examples(spy + aapl)[0]

    assert first.label_end_at - first.observed_at == timedelta(hours=4)


def test_skips_label_when_missing_bars_stretch_the_four_hour_horizon() -> None:
    spy = bars("SPY", [100.0 + index for index in range(120)])
    aapl = bars("AAPL", [200.0 + index for index in range(120)])
    missing_at = aapl[70].timestamp
    aapl = [bar for bar in aapl if bar.timestamp != missing_at]

    examples = build_training_examples(spy + aapl)

    assert all(item.label_end_at - item.observed_at == timedelta(hours=4) for item in examples)


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


def test_economic_labels_exclude_returns_that_do_not_clear_costs() -> None:
    start = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
    examples = (
        example("AAPL", start, 0.0020),
        example("MSFT", start, -0.0015),
        example("NVDA", start, 0.0005),
        example("AMZN", start, -0.0009),
    )

    labeled = economically_labeled_examples(examples, cost_bps=10)

    assert [(item.symbol, label) for item, label in labeled] == [
        ("AAPL", True),
        ("MSFT", False),
    ]


def test_rejects_tampered_bar_cache(tmp_path: Path) -> None:
    cache = tmp_path / "bars.csv"
    cache.write_text("original")
    specification = BarCacheSpecification("2025-01-01", "2026-01-01", ("SPY",), 5)
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
