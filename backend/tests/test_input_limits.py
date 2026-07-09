"""Request body-size limit + input caps on the public capture endpoints."""


def test_oversized_body_is_rejected_413(api):
    # The body-size middleware runs before routing/validation, so a huge body is
    # rejected regardless of the target route.
    big = b'{"client_id":"x","session_key":"y","junk":"' + b"A" * 600_000 + b'"}'
    r = api.post(
        "/api/track/landing", content=big, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_overlong_field_is_rejected_422(api):
    r = api.post(
        "/api/track/landing",
        json={"client_id": "x", "session_key": "y", "utm_source": "A" * 600},
    )
    assert r.status_code == 422  # utm_source capped at 512


def test_too_many_click_ids_rejected_422(api):
    r = api.post(
        "/api/track/landing",
        json={
            "client_id": "x",
            "session_key": "y",
            "click_ids": {f"k{i}": "v" for i in range(50)},
        },
    )
    assert r.status_code == 422
