from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal, Protocol, cast
from urllib.parse import urlparse

import boto3
from pydantic import BaseModel, ConfigDict, model_validator
from xgboost import DMatrix

from catalyst_router.training import FEATURE_NAMES, FEATURE_SCHEMA, FeatureVector


class _ModelArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    sha256: str


class _FoldResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cumulative_return: float


class _ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    folds: int
    fold_results: list[_FoldResult]
    mean_cumulative_return: float
    mean_sharpe: float

    @model_validator(mode="after")
    def validate_fold_count(self) -> _ValidationSummary:
        if self.folds != len(self.fold_results):
            raise ValueError("validation fold count does not match fold results")
        return self


class _HoldoutSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cumulative_return: float
    annualized_sharpe: float
    max_drawdown: float
    trades: int


class _ChallengerManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authority: Literal["SHADOW_ONLY"]
    run_id: str
    created_at: str
    candidate: str
    feature_schema: str
    feature_names: list[str]
    prediction_kind: Literal["probability", "return"]
    decision_gate: float
    horizon_bars: int
    timeframe_minutes: int
    symbols: list[str]
    selection_score: float
    numeric_shadow_gate_passed: bool
    promotion_eligible: Literal[False]
    model: _ModelArtifact
    validation: _ValidationSummary
    holdout: _HoldoutSummary

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> _ChallengerManifest:
        if self.feature_schema != FEATURE_SCHEMA or tuple(self.feature_names) != FEATURE_NAMES:
            raise ValueError("challenger feature schema does not match runtime schema")
        if self.timeframe_minutes != 15:
            raise ValueError("challenger timeframe must be 15 minutes")
        if "SPY" not in self.symbols or len(self.symbols) != len(set(self.symbols)):
            raise ValueError("challenger symbols must be unique and include SPY")
        if self.prediction_kind == "probability" and not 0.5 <= self.decision_gate <= 1:
            raise ValueError("probability decision gate must be between 0.5 and 1")
        if self.prediction_kind == "return" and self.decision_gate < 0:
            raise ValueError("return decision gate must be nonnegative")
        return self


class PublicChallengerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployed: bool
    loaded: bool
    authority: Literal["SHADOW_ONLY"] | None = None
    run_id: str | None = None
    created_at: str | None = None
    candidate: str | None = None
    feature_schema: str | None = None
    decision_gate: float | None = None
    horizon_bars: int | None = None
    timeframe_minutes: int | None = None
    symbol_count: int | None = None
    selection_score: float | None = None
    validation_return: float | None = None
    validation_sharpe: float | None = None
    positive_folds: int | None = None
    folds: int | None = None
    holdout_return: float | None = None
    holdout_sharpe: float | None = None
    holdout_max_drawdown: float | None = None
    holdout_trades: int | None = None
    numeric_shadow_gate_passed: bool | None = None
    promotion_eligible: bool = False
    model_sha256: str | None = None

    @classmethod
    def not_deployed(cls) -> PublicChallengerStatus:
        return cls(deployed=False, loaded=False)


@dataclass(frozen=True, slots=True)
class ShadowPrediction:
    symbol: str
    observed_at: datetime
    run_id: str
    value: float
    signal: Literal["LONG", "SHORT", "ABSTAIN"]


class _PredictionValues(Protocol):
    def __getitem__(self, index: int) -> float: ...


class _PredictiveModel(Protocol):
    def predict(self, data: DMatrix) -> _PredictionValues: ...


@dataclass(slots=True)
class ShadowChallenger:
    status: PublicChallengerStatus
    feature_names: tuple[str, ...]
    prediction_kind: Literal["probability", "return"]
    symbols: tuple[str, ...]
    _model: _PredictiveModel = field(repr=False)

    def predict(self, vector: FeatureVector) -> ShadowPrediction:
        if vector.schema != self.status.feature_schema:
            raise ValueError("feature schema does not match deployed challenger")
        if vector.names != self.feature_names or vector.names != FEATURE_NAMES:
            raise ValueError("feature names do not match deployed challenger")
        value = float(
            self._model.predict(DMatrix([vector.values], feature_names=list(self.feature_names)))[0]
        )
        gate = self.status.decision_gate
        run_id = self.status.run_id
        if gate is None or run_id is None:
            raise RuntimeError("deployed challenger metadata is incomplete")
        if self.prediction_kind == "probability":
            signal: Literal["LONG", "SHORT", "ABSTAIN"]
            if value >= gate:
                signal = "LONG"
            elif value <= 1 - gate:
                signal = "SHORT"
            else:
                signal = "ABSTAIN"
        else:
            signal = "LONG" if value >= gate else "SHORT" if value <= -gate else "ABSTAIN"
        return ShadowPrediction(
            symbol=vector.symbol,
            observed_at=vector.observed_at,
            run_id=run_id,
            value=value,
            signal=signal,
        )

    @classmethod
    def load_from_s3(
        cls, manifest_uri: str, region: str, expected_manifest_sha256: str
    ) -> ShadowChallenger:
        parsed = urlparse(manifest_uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
            raise ValueError("CHALLENGER_MANIFEST_URI must be an s3:// URI")

        bucket = parsed.netloc
        manifest_key = parsed.path.lstrip("/")
        s3 = boto3.client("s3", region_name=region)
        manifest_bytes = s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_hash != expected_manifest_sha256:
            raise RuntimeError("deployed challenger manifest checksum mismatch")
        manifest = _ChallengerManifest.model_validate_json(manifest_bytes)
        model_name = PurePosixPath(manifest.model.path).name
        model_key = str(PurePosixPath(manifest_key).parent / model_name)
        model_bytes = s3.get_object(Bucket=bucket, Key=model_key)["Body"].read()
        actual_hash = hashlib.sha256(model_bytes).hexdigest()
        if actual_hash != manifest.model.sha256:
            raise RuntimeError("deployed challenger model checksum mismatch")

        from xgboost import Booster

        model = Booster()
        model.load_model(bytearray(model_bytes))

        positive_folds = sum(
            fold.cumulative_return > 0 for fold in manifest.validation.fold_results
        )
        return cls(
            status=PublicChallengerStatus(
                deployed=True,
                loaded=True,
                authority=manifest.authority,
                run_id=manifest.run_id,
                created_at=manifest.created_at,
                candidate=manifest.candidate,
                feature_schema=manifest.feature_schema,
                decision_gate=manifest.decision_gate,
                horizon_bars=manifest.horizon_bars,
                timeframe_minutes=manifest.timeframe_minutes,
                symbol_count=len(manifest.symbols),
                selection_score=manifest.selection_score,
                validation_return=manifest.validation.mean_cumulative_return,
                validation_sharpe=manifest.validation.mean_sharpe,
                positive_folds=positive_folds,
                folds=manifest.validation.folds,
                holdout_return=manifest.holdout.cumulative_return,
                holdout_sharpe=manifest.holdout.annualized_sharpe,
                holdout_max_drawdown=manifest.holdout.max_drawdown,
                holdout_trades=manifest.holdout.trades,
                numeric_shadow_gate_passed=manifest.numeric_shadow_gate_passed,
                promotion_eligible=False,
                model_sha256=manifest.model.sha256,
            ),
            feature_names=tuple(manifest.feature_names),
            prediction_kind=manifest.prediction_kind,
            symbols=tuple(manifest.symbols),
            _model=cast(_PredictiveModel, model),
        )
