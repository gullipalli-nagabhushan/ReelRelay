"""Ephemeral download progress registry."""

from __future__ import annotations

import time

from core.progress import ProgressRegistry


def test_progress_lifecycle() -> None:
    registry = ProgressRegistry()
    registry.create("tok-1", "clip.mp4", 1000)

    snapshot = registry.get("tok-1")
    assert snapshot is not None
    assert snapshot["state"] == "preparing"
    assert snapshot["total"] == 1000
    assert snapshot["percent"] == 0.0

    registry.begin("tok-1", 1000)
    registry.update("tok-1", 500, 1000)
    snapshot = registry.get("tok-1")
    assert snapshot["state"] == "streaming"
    assert snapshot["sent"] == 500
    assert snapshot["percent"] == 50.0
    assert snapshot["elapsed"] >= 0

    registry.finish("tok-1")
    snapshot = registry.get("tok-1")
    assert snapshot["state"] == "done"
    assert snapshot["sent"] == 1000
    assert snapshot["percent"] == 100.0
    assert snapshot["speed"] == 0


def test_progress_error_state() -> None:
    registry = ProgressRegistry()
    registry.create("tok-2", "clip.mp3")
    registry.finish("tok-2", "The stream was interrupted.")

    snapshot = registry.get("tok-2")
    assert snapshot["state"] == "error"
    assert "interrupted" in snapshot["error"]


def test_unknown_token_returns_none() -> None:
    registry = ProgressRegistry()
    assert registry.get("missing") is None


def test_registry_is_bounded_and_reusable() -> None:
    registry = ProgressRegistry()
    registry.create("same", "first.mp4", 10)
    registry.create("same", "second.mp4", 20)

    snapshot = registry.get("same")
    assert snapshot["filename"] == "second.mp4"
    assert snapshot["total"] == 20
    assert len(registry) == 1

    registry.clear()
    assert len(registry) == 0


def test_estimated_progress_never_reaches_100_until_done() -> None:
    registry = ProgressRegistry()
    registry.create("est", "clip.mp3", 1000, estimated=True)
    registry.begin("est", 1000, estimated=True)
    registry.update("est", 1000, 1000, estimated=True)

    snapshot = registry.get("est")
    assert snapshot["total_is_estimate"] is True
    assert snapshot["percent"] == 99.9
    assert snapshot["remaining"] == 0

    registry.finish("est")
    snapshot = registry.get("est")
    assert snapshot["state"] == "done"
    assert snapshot["percent"] == 100.0
    # Estimated totals are not forced onto the byte count.
    assert snapshot["sent"] == 1000
    assert snapshot["eta"] == 0.0


def test_eta_and_remaining_use_measured_speed() -> None:
    registry = ProgressRegistry()
    registry.create("eta", "clip.mp4", 1000)
    registry.begin("eta", 1000)
    with registry._lock:
        entry = registry._entries["eta"]
        entry["sent"] = 400
        entry["speed"] = 100.0
        entry["updated_at"] = time.time()

    snapshot = registry.get("eta")
    assert snapshot["remaining"] == 600
    assert snapshot["eta"] == 6.0
    assert snapshot["percent"] == 40.0
    assert snapshot["speed"] == 100
    assert snapshot["stalled"] is False


def test_stalled_stream_stops_reporting_speed() -> None:
    registry = ProgressRegistry()
    registry.create("stall", "clip.mp4", 1000)
    registry.begin("stall", 1000)
    with registry._lock:
        entry = registry._entries["stall"]
        entry["sent"] = 900
        entry["speed"] = 500.0
        entry["updated_at"] = time.time() - 10.0

    snapshot = registry.get("stall")
    assert snapshot["speed"] == 0
    assert snapshot["eta"] is None
    assert snapshot["stalled"] is True
    assert snapshot["remaining"] == 100


def test_relay_finish_snaps_to_exact_total() -> None:
    registry = ProgressRegistry()
    registry.create("relay", "clip.mp4", 1000)
    registry.begin("relay", 1000)
    registry.update("relay", 990, 1000)
    registry.finish("relay")

    snapshot = registry.get("relay")
    assert snapshot["percent"] == 100.0
    assert snapshot["sent"] == 1000
    assert snapshot["total_is_estimate"] is False


def test_elapsed_keeps_counting_when_streaming_begins() -> None:
    registry = ProgressRegistry()
    registry.create("elapsed", "clip.mp4", 100)
    with registry._lock:
        registry._entries["elapsed"]["started_at"] = time.time() - 5.0

    registry.begin("elapsed", 100)
    snapshot = registry.get("elapsed")
    assert snapshot["elapsed"] >= 4.5
    assert snapshot["state"] == "streaming"


def test_restart_clears_time_spent_on_the_confirmation_dialog() -> None:
    registry = ProgressRegistry()
    registry.create("confirm", "clip.mp4", 100)
    registry.begin("confirm", 100)
    registry.update("confirm", 50, 100)
    with registry._lock:
        registry._entries["confirm"]["started_at"] = time.time() - 30.0

    registry.create("confirm", "clip.mp4", 100, restart=True)

    snapshot = registry.get("confirm")
    assert snapshot["state"] == "preparing"
    assert snapshot["sent"] == 0
    assert snapshot["elapsed"] < 1.0
