import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest

from catalyst_router import challenger
from catalyst_router.training import FEATURE_NAMES, FEATURE_SCHEMA, FeatureVector


class _Body:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def read(self) -> bytes:
        return self._value


def test_rejects_untrusted_manifest_before_loading_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = b"{}"

    class FakeS3:
        def get_object(self, **_: str) -> dict[str, _Body]:
            return {"Body": _Body(manifest)}

    monkeypatch.setattr(challenger.boto3, "client", lambda *_args, **_kwargs: FakeS3())

    with pytest.raises(RuntimeError, match="manifest checksum mismatch"):
        challenger.ShadowChallenger.load_from_s3(
            "s3://private-bucket/models/run/manifest.json",
            "us-east-1",
            hashlib.sha256(b"different manifest").hexdigest(),
        )


def test_predicts_only_exact_deployed_feature_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeMatrix:
        def __init__(self, values: list[tuple[float, ...]], *, feature_names: list[str]) -> None:
            captured.update(values=values, feature_names=feature_names)

    class FakeModel:
        def predict(self, _matrix: FakeMatrix) -> list[float]:
            return [0.73]

    monkeypatch.setattr(challenger, "DMatrix", FakeMatrix)
    deployed = challenger.ShadowChallenger(
        status=challenger.PublicChallengerStatus(
            deployed=True,
            loaded=True,
            authority="SHADOW_ONLY",
            run_id="run-1",
            feature_schema=FEATURE_SCHEMA,
            decision_gate=0.6,
            model_sha256="a" * 64,
        ),
        feature_names=FEATURE_NAMES,
        prediction_kind="probability",
        symbols=("SPY",),
        _model=FakeModel(),
    )
    vector = FeatureVector(
        symbol="SPY",
        observed_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
        schema=FEATURE_SCHEMA,
        names=FEATURE_NAMES,
        values=(0.0,) * len(FEATURE_NAMES),
    )

    prediction = deployed.predict(vector)

    assert prediction.value == 0.73
    assert prediction.signal == "LONG"
    assert captured == {
        "values": [vector.values],
        "feature_names": list(FEATURE_NAMES),
    }

    with pytest.raises(ValueError, match="feature schema"):
        deployed.predict(
            FeatureVector(
                symbol="SPY",
                observed_at=vector.observed_at,
                schema="wrong",
                names=FEATURE_NAMES,
                values=vector.values,
            )
        )
