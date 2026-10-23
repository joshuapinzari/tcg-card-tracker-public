from services.analysis.graded_premium_signal import (
    _heat_bucket,
)


def test_heat_bucket_boundaries():
    assert _heat_bucket(
        0.10
    ) == "COOL"

    assert _heat_bucket(
        0.20
    ) == "COOL"

    assert _heat_bucket(
        0.21
    ) == "BELOW AVG"

    assert _heat_bucket(
        0.40
    ) == "BELOW AVG"

    assert _heat_bucket(
        0.50
    ) == "NEUTRAL"

    assert _heat_bucket(
        0.80
    ) == "HOT"

    assert _heat_bucket(
        0.81
    ) == "VERY HOT"


def test_none_heat_bucket():
    assert (
        _heat_bucket(
            None
        )
        is None
    )
