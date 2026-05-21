from __future__ import annotations

import sys
import types

from quest3server.vision.metrics import elapsed_ms, sample_gpu_memory_mb


def test_elapsed_ms_uses_perf_counter_delta(monkeypatch) -> None:
    perf_counter_values = iter([100.0, 100.125])

    fake_time = types.SimpleNamespace(perf_counter=lambda: next(perf_counter_values))
    monkeypatch.setattr("quest3server.vision.metrics.time", fake_time)

    started_at = fake_time.perf_counter()
    assert elapsed_ms(started_at) == 125.0


def test_sample_gpu_memory_mb_returns_none_without_torch(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "torch":
            raise ImportError("torch unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert sample_gpu_memory_mb() is None


def test_sample_gpu_memory_mb_returns_none_when_cuda_is_unavailable(monkeypatch) -> None:
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: False,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert sample_gpu_memory_mb() is None


def test_sample_gpu_memory_mb_reads_cuda_memory(monkeypatch) -> None:
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            memory_allocated=lambda: 3 * 1024 * 1024,
            max_memory_allocated=lambda: 5 * 1024 * 1024,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    result = sample_gpu_memory_mb()

    assert result is not None
    assert result.allocated == 3.0
    assert result.max_allocated == 5.0
