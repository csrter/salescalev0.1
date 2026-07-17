"""Timezone normalization for campaign send windows (the Q2 CPA "not
sending" bug: an 'MST' campaign silently fell back to UTC and parked every
enrollment to the next UTC window)."""

import datetime as dt

import pytest

from app.services import email_campaigns, timezones


def test_normalize_maps_abbreviations():
    assert timezones.normalize("MST") == "America/Denver"
    assert timezones.normalize("est") == "America/New_York"
    assert timezones.normalize("PST") == "America/Los_Angeles"
    assert timezones.normalize("UTC") == "UTC"
    # A real IANA key passes through unchanged.
    assert timezones.normalize("America/Phoenix") == "America/Phoenix"
    # Garbage is unresolvable.
    assert timezones.normalize("Narnia/Cair") is None
    assert timezones.normalize("") is None
    assert timezones.normalize(None) is None


def test_resolve_never_silently_wrong_for_abbreviations():
    # The core bug: ZoneInfo('MST') raises, so _tz used to fall back to UTC.
    # resolve() maps it to a real -7/-6 zone instead.
    tz = timezones.resolve("MST")
    off = dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc).astimezone(tz).utcoffset()
    assert off == dt.timedelta(hours=-6)  # Denver is on MDT in July
    # Truly unresolvable → UTC fallback (logged, not a crash).
    assert timezones.resolve("Narnia") == dt.timezone.utc


class _Campaign:
    def __init__(self, tz):
        self.timezone = tz
        self.send_days = [0, 1, 2, 3, 4]
        self.send_window_start = 8
        self.send_window_end = 17


def test_next_valid_send_time_uses_real_offset_not_utc():
    """Friday 20:00 UTC = 13:00 in US Mountain — inside an 8-17 window, so a
    send is due NOW, not parked to Monday (the UTC-fallback symptom)."""
    friday_2001 = dt.datetime(2026, 7, 17, 20, 1, tzinfo=dt.timezone.utc)
    when = email_campaigns._next_valid_send_time(friday_2001, _Campaign("MST"))
    assert when is not None
    # Inside the window → returns ~now, same day (not the following Monday).
    assert when.date() == friday_2001.date()

    # Contrast: with a bare 'UTC' campaign, 20:01 is past the 8-17 UTC window,
    # so it correctly rolls to the next weekday (Monday the 20th).
    when_utc = email_campaigns._next_valid_send_time(friday_2001, _Campaign("UTC"))
    assert when_utc.date() == dt.date(2026, 7, 20)
