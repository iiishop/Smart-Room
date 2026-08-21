import http.server
import threading
import time
from types import SimpleNamespace

import pytest

import quest3_rgbd_align_viewer as viewer


def _profile(index: int) -> dict:
    return {
        "canonical_device_id": f"device-{index}",
        "display_name": f"Device {index}",
        "summary": "MQTT device",
        "device_type": "network_device",
        "capabilities": [],
        "protocols": ["MQTT"],
        "identifiers": {"mqtt_topic_prefix": [f"test/device/{index}"]},
        "connections": {},
        "data": {},
        "operations": [],
    }


def _complete_payload(profiles: list[dict]) -> dict:
    return {
        "candidates": [
            {
                "candidate_id": profile["canonical_device_id"],
                "relation": "unknown",
                "verdicts": ["unknown"] * len(viewer.PAIRING_RULES),
            }
            for profile in profiles
        ]
    }


def test_openrouter_pairing_uses_one_prompt_json_request(monkeypatch) -> None:
    profiles = [_profile(1)]
    calls = []

    def fake_completion(_config, _messages, **kwargs):
        calls.append(kwargs)
        payload = _complete_payload(profiles)
        payload["candidates"][0]["verdicts"][3] = "unrelated"
        return viewer.json.dumps(payload), "stop"

    monkeypatch.setattr(viewer, "_vlm_chat_completion_detail", fake_completion)
    payload, _raw, _finish, status = viewer._pairing_llm_review(
        {
            "base_url": "https://openrouter.ai/api/v1",
            "pairing_model": "qwen/qwen3.7-plus",
            "pairing_reasoning_effort": "minimal",
        },
        viewer.build_pairing_prompt({"summary": "device"}, profiles),
        profiles,
        30.0,
    )

    assert len(calls) == 1
    assert calls[0].get("response_format") == {"type": "json_object"}
    assert status == "prompt_json"
    assert payload is not None
    assert payload["candidates"][0]["verdicts"][3] == "unknown"


def test_pairing_batches_run_in_one_parallel_wave(monkeypatch) -> None:
    profiles = [_profile(index) for index in range(50)]
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_review(_config, _prompt, batch_profiles, _timeout):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _complete_payload(batch_profiles), "{}", "stop", "test"

    monkeypatch.setattr(viewer, "_pairing_llm_review", fake_review)
    payload, diagnostics, warning = viewer._pairing_llm_review_batches(
        {},
        {"summary": "device"},
        profiles,
        30.0,
    )

    assert max_active == 5
    assert len(diagnostics) == 5
    assert len(payload["candidates"]) == 50
    assert warning == ""


def test_pairing_does_not_retry_partial_batch(monkeypatch) -> None:
    profiles = [_profile(index) for index in range(10)]
    reviewed_sizes = []

    def fake_review(_config, _prompt, batch_profiles, _timeout):
        reviewed_sizes.append(len(batch_profiles))
        selected = batch_profiles[:-1]
        return _complete_payload(selected), "{}", "stop", "test"

    monkeypatch.setattr(viewer, "_pairing_llm_review", fake_review)
    payload, diagnostics, warning = viewer._pairing_llm_review_batches(
        {},
        {"summary": "device"},
        profiles,
        30.0,
    )

    assert reviewed_sizes == [10]
    assert len(payload["candidates"]) == 9
    assert diagnostics[0]["candidate_count"] == 9
    assert "remain selectable" in warning


def test_pairing_batch_has_hard_total_timeout(monkeypatch) -> None:
    profiles = [_profile(index) for index in range(20)]

    def fake_review(_config, _prompt, _batch_profiles, _timeout):
        time.sleep(0.3)
        return None, "", "", "late"

    monkeypatch.setattr(viewer, "_pairing_llm_review", fake_review)
    started = time.monotonic()
    payload, diagnostics, warning = viewer._pairing_llm_review_batches(
        {},
        {"summary": "device"},
        profiles,
        0.05,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert payload is None
    assert all("total timeout" in item["status"] for item in diagnostics)
    assert "remain selectable" in warning


def test_vlm_http_request_uses_total_timeout() -> None:
    class SlowHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(b'{"partial":')
                self.wfile.flush()
                for _ in range(20):
                    time.sleep(0.03)
                    self.wfile.write(b" ")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, _format, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    server.daemon_threads = True
    server.block_on_close = False
    worker = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        daemon=True,
    )
    worker.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="total timeout"):
            viewer._vlm_json_request(
                "POST",
                f"http://127.0.0.1:{server.server_port}/slow",
                "",
                payload={"test": True},
                timeout=0.12,
            )
    finally:
        server.shutdown()
        server.server_close()

    assert time.monotonic() - started < 0.5


def test_active_pairing_job_is_not_marked_stale(tmp_path) -> None:
    record = {
        "object_id": "object-1",
        "pairing_status": "processing",
        "pairing_started_at_ms": 1,
        "pairing_heartbeat_at_ms": 1,
    }
    store = {
        "room_id": "room-1",
        "device_id": "quest-1",
        "objects": [record],
        "images": [],
        "points": [],
    }
    fake_viewer = SimpleNamespace(
        _analysis_jobs_lock=threading.Lock(),
        _pairing_jobs={("room-1", "quest-1", "object-1")},
    )
    args = SimpleNamespace(pairing_timeout_seconds=15.0, vlm_timeout_seconds=120.0)

    changed = viewer._mark_stale_vlm_records(args, tmp_path, store, fake_viewer)

    assert changed is False
    assert record["pairing_status"] == "processing"


def test_data_write_prefers_operation_and_falls_back_to_source_topic() -> None:
    profile = {
        "identifiers": {"mqtt_entity_prefix": ["site/device"]},
        "operations": [
            {"topic": "site/device/POWER", "action": "set", "sensor_key": ""},
            {"topic": "site/device/brightness/set", "action": "set", "sensor_key": "brightness"},
        ],
        "data": {
            "temperature": {
                "value": 23.0,
                "source_topic": "site/device/telemetry",
                "source_payload": {"temperature": 23.0, "humidity": 40.0},
                "payload_path": ["temperature"],
            },
            "voltage": {"value": 230.0},
        },
    }

    assert viewer._resolve_data_write_operation(profile, "power")["topic"] == "site/device/POWER"
    assert viewer._resolve_data_write_operation(profile, "brightness")["topic"] == "site/device/brightness/set"
    assert viewer._resolve_data_write_operation(profile, "temperature") is None
    assert (
        viewer._data_source_publish_topic(profile, "temperature", profile["data"]["temperature"])
        == "site/device/telemetry"
    )
    assert (
        viewer._data_source_publish_topic(profile, "voltage", profile["data"]["voltage"])
        == "site/device"
    )
    assert viewer._data_write_payload(
        profile["data"]["temperature"],
        "temperature",
        24.5,
    ) == {"temperature": 24.5, "humidity": 40.0}

    live = viewer._live_network_profile_payload(profile)
    assert all(row["writable"] for row in live["data"])


def test_restore_edit_preserves_binding_and_async_analysis(tmp_path) -> None:
    object_id = "object-1"
    edit_session_id = "edit-1"
    backup_dir = tmp_path / ".object_edit_backups" / edit_session_id
    backup_dir.mkdir(parents=True)
    backup_store = {
        "objects": [{"object_id": object_id, "status": "completed", "point_count": 1}],
        "images": [],
        "points": [{"object_id": object_id, "point_id": "original"}],
    }
    (backup_dir / "points.json").write_text(viewer.json.dumps(backup_store), encoding="utf-8")
    (backup_dir / "manifest.json").write_text(
        viewer.json.dumps({"object_id": object_id, "image_ids": [], "file_backups": []}),
        encoding="utf-8",
    )
    current_store = {
        "objects": [
            {
                "object_id": object_id,
                "status": "completed",
                "network_binding": {"canonical_device_id": "network-1"},
                "binding_history": [{"action": "bound"}],
                "vlm_status": "done",
                "vlm_profile": {"device_type": "lamp"},
                "pairing_status": "done",
                "pairing_candidates": [{"canonical_device_id": "network-1"}],
            }
        ],
        "images": [],
        "points": [{"object_id": object_id, "point_id": "edited"}],
    }
    (tmp_path / "points.json").write_text(viewer.json.dumps(current_store), encoding="utf-8")

    restored = viewer._restore_object_edit_backup(tmp_path, edit_session_id)
    record = viewer._find_object_record(restored, object_id)

    assert [point["point_id"] for point in restored["points"]] == ["original"]
    assert record["network_binding"]["canonical_device_id"] == "network-1"
    assert record["vlm_status"] == "done"
    assert record["pairing_status"] == "done"


def test_viewer_http_server_handles_requests_concurrently() -> None:
    assert issubclass(viewer._ViewerHttpServer, http.server.ThreadingHTTPServer)
    assert viewer._ViewerHttpServer.daemon_threads is True


def test_send_json_treats_client_disconnect_as_normal() -> None:
    class DisconnectingWriter:
        def write(self, _body):
            raise ConnectionAbortedError(10053, "client closed")

    handler = SimpleNamespace(
        send_response=lambda _status: None,
        send_header=lambda _key, _value: None,
        end_headers=lambda: None,
        wfile=DisconnectingWriter(),
        close_connection=False,
        client_address=("127.0.0.1", 1234),
    )

    viewer._PayloadHandler._send_json(handler, 200, {"ok": True})

    assert handler.close_connection is True
