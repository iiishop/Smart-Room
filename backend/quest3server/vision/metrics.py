from __future__ import annotations

import time

from .types import GpuMemoryStats


def start_timer() -> float:
    return time.perf_counter()


def elapsed_ms(start_time: float) -> float:
    return (time.perf_counter() - start_time) * 1000.0


def sample_gpu_memory_mb() -> GpuMemoryStats | None:
    try:
        import torch
    except ImportError:
        return None

    if not torch.cuda.is_available():
        return None

    allocated = torch.cuda.memory_allocated() / (1024 * 1024)
    max_allocated = torch.cuda.max_memory_allocated() / (1024 * 1024)
    return GpuMemoryStats(
        allocated=float(allocated),
        max_allocated=float(max_allocated),
    )
