from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd
from pydantic import BaseModel, ConfigDict

from catalyst_router.training import BarCacheSpecification, sha256_file

_BAR_COLUMNS = {"symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"}


class DatasetPartition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    symbol: str
    year: int
    rows: int
    sha256: str


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    created_at: datetime
    feed: str
    adjustment: str
    start: str
    end: str
    timeframe_minutes: int
    symbols: tuple[str, ...]
    rows: int
    partitions: tuple[DatasetPartition, ...]


def write_bar_dataset(
    frame: pd.DataFrame,
    destination: Path,
    specification: BarCacheSpecification,
) -> Path:
    missing = _BAR_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"bar dataset is missing columns: {sorted(missing)}")
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"dataset destination is not empty: {destination}")

    normalized = frame.copy()
    normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True)
    normalized["symbol"] = normalized["symbol"].astype(str).str.upper()
    if normalized.duplicated(subset=["symbol", "timestamp"]).any():
        raise ValueError("bar dataset contains duplicate symbol timestamps")
    if set(normalized["symbol"]) != set(specification.symbols):
        raise ValueError("bar dataset symbols do not match its specification")
    normalized = normalized.sort_values(["symbol", "timestamp"])
    normalized["year"] = normalized["timestamp"].dt.year

    destination.mkdir(parents=True, exist_ok=False)
    partitions = []
    for key, partition in normalized.groupby(["symbol", "year"], sort=True):
        symbol, year = cast(tuple[str, int], key)
        relative = Path(f"symbol={symbol}") / f"year={int(year)}" / "bars.parquet"
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=False)
        partition.drop(columns=["year"]).to_parquet(
            path,
            index=False,
            compression="zstd",
            engine="pyarrow",
        )
        partitions.append(
            DatasetPartition(
                path=relative.as_posix(),
                symbol=str(symbol),
                year=int(year),
                rows=len(partition),
                sha256=sha256_file(path),
            )
        )

    manifest = DatasetManifest(
        schema_version="market-bars-v1",
        created_at=datetime.now(UTC),
        feed="iex",
        adjustment="raw",
        start=specification.start,
        end=specification.end,
        timeframe_minutes=specification.timeframe_minutes,
        symbols=specification.symbols,
        rows=len(normalized),
        partitions=tuple(partitions),
    )
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n")
    return manifest_path


def validate_bar_dataset(manifest_path: Path) -> DatasetManifest:
    manifest = DatasetManifest.model_validate_json(manifest_path.read_bytes())
    rows = 0
    for partition in manifest.partitions:
        path = manifest_path.parent / partition.path
        if not path.is_file() or sha256_file(path) != partition.sha256:
            raise RuntimeError(f"dataset partition checksum mismatch: {partition.path}")
        frame = pd.read_parquet(path, columns=["symbol", "timestamp"], engine="pyarrow")
        timestamps = pd.to_datetime(frame["timestamp"], utc=True)
        if (
            len(frame) != partition.rows
            or set(frame["symbol"]) != {partition.symbol}
            or set(timestamps.dt.year) != {partition.year}
            or frame.duplicated(subset=["symbol", "timestamp"]).any()
            or (timestamps.dt.second != 0).any()
            or (timestamps.dt.minute % manifest.timeframe_minutes != 0).any()
        ):
            raise RuntimeError(f"dataset partition metadata mismatch: {partition.path}")
        rows += partition.rows
    if rows != manifest.rows:
        raise RuntimeError("dataset manifest row count does not match partitions")
    return manifest
