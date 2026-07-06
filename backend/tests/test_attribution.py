"""Definition-of-Done: a landing-page visit captures UTM parameters (and
click IDs — the Phase 5 extension point) into landing_events."""


def test_landing_capture_records_utms(api, seeded):
    payload = {
        "client_id": seeded["client_a"],
        "session_key": "visitor-123",
        "landing_url": "https://alphahvac.com/ac-repair?utm_source=facebook",
        "utm_source": "facebook",
        "utm_medium": "paid_social",
        "utm_campaign": "summer_ac_tuneup",
        "utm_content": "video_a",
        "utm_term": "ac repair",
        "referrer": "https://l.facebook.com/",
        "fbclid": "IwAR123example",
    }
    resp = api.post("/api/track/landing", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["utm_source"] == "facebook"
    assert body["utm_campaign"] == "summer_ac_tuneup"
    assert body["utm_term"] == "ac repair"
    assert body["fbclid"] == "IwAR123example"
    assert body["gclid"] is None
    assert body["client_id"] == seeded["client_a"]
    assert body["occurred_at"] is not None


def test_landing_capture_rejects_unknown_client(api):
    resp = api.post(
        "/api/track/landing",
        json={"client_id": "not-a-client", "session_key": "x"},
    )
    assert resp.status_code == 404


def test_captured_event_visible_to_team(api, team_headers, seeded):
    resp = api.get(
        f"/api/attribution/landing-events?client_id={seeded['client_a']}",
        headers=team_headers,
    )
    assert resp.status_code == 200
    sessions = [e["session_key"] for e in resp.json()]
    assert "visitor-123" in sessions
