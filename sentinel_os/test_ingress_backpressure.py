"""Tests for the ingress pause that slows submissions as the queue fills."""

import pytest
import redis


class FakeQueue:
    def __init__(self, depth=0, error=None):
        self.depth = depth
        self.error = error

    def ready_depth(self):
        if self.error is not None:
            raise self.error
        return self.depth


@pytest.fixture
def api():
    # Imported lazily: api_server_v2 reads its queue settings from the
    # environment, which the API test module sets up before importing it.
    import api_server_v2

    return api_server_v2


@pytest.fixture
def sleeps(monkeypatch, api):
    recorded = []
    monkeypatch.setattr(api.time, "sleep", recorded.append)
    monkeypatch.setattr(api, "TRANSMISSION_QUEUE_CAPACITY", 10_000)
    return recorded


def test_empty_queue_does_not_pause(api, sleeps):
    api._ingress_pause(FakeQueue(depth=0))
    assert sleeps == []


def test_below_watermark_does_not_pause(api, sleeps):
    api._ingress_pause(FakeQueue(depth=8_000))
    assert sleeps == []


def test_full_queue_pauses_for_the_ceiling(api, sleeps):
    api._ingress_pause(FakeQueue(depth=10_000))
    assert sleeps == [api.INGRESS_MAX_DELAY_SECONDS]


def test_partly_full_queue_pauses_between_zero_and_ceiling(api, sleeps):
    api._ingress_pause(FakeQueue(depth=9_500))
    assert len(sleeps) == 1
    assert 0.0 < sleeps[0] < api.INGRESS_MAX_DELAY_SECONDS


def test_zero_capacity_turns_pause_off(api, sleeps, monkeypatch):
    monkeypatch.setattr(api, "TRANSMISSION_QUEUE_CAPACITY", 0)
    api._ingress_pause(FakeQueue(depth=10_000))
    assert sleeps == []


def test_redis_failure_is_reported_as_unavailable(api, sleeps):
    with pytest.raises(api.HTTPException) as exc:
        api._ingress_pause(FakeQueue(error=redis.exceptions.ConnectionError("down")))
    assert exc.value.status_code == 503
    assert sleeps == []
