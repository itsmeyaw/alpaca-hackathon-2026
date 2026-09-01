from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from catalyst_router.datasets import validate_bar_dataset, write_bar_dataset
from catalyst_router.training import BarCacheSpecification


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "timestamp": datetime(2025, 1, 2, 14, 30 + offset * 5, tzinfo=UTC),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": 1_000,
                "vwap": price + 0.25,
            }
            for symbol, price in (("SPY", 500.0), ("AAPL", 200.0))
            for offset in range(2)
        ]
    )


def test_writes_and_validates_partitioned_immutable_bar_dataset(tmp_path: Path) -> None:
    destination = tmp_path / "dataset-1"
    specification = BarCacheSpecification("2025-01-01", "2025-12-31", ("SPY", "AAPL"), 5)

    manifest_path = write_bar_dataset(frame(), destination, specification)
    manifest = validate_bar_dataset(manifest_path)

    assert manifest.schema_version == "market-bars-v1"
    assert manifest.rows == 4
    assert {item.path for item in manifest.partitions} == {
        "symbol=AAPL/year=2025/bars.parquet",
        "symbol=SPY/year=2025/bars.parquet",
    }
    with pytest.raises(FileExistsError):
        write_bar_dataset(frame(), destination, specification)


def test_rejects_tampered_dataset_partition(tmp_path: Path) -> None:
    destination = tmp_path / "dataset-1"
    manifest_path = write_bar_dataset(
        frame(),
        destination,
        BarCacheSpecification("2025-01-01", "2025-12-31", ("SPY", "AAPL"), 5),
    )
    partition = next(destination.glob("**/*.parquet"))
    partition.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        validate_bar_dataset(manifest_path)
