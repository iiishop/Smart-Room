from __future__ import annotations

from typing import Any

import numpy as np


def encode_binary_mask(mask: np.ndarray) -> dict[str, Any]:
    array = np.asarray(mask, dtype=np.uint8)
    if array.ndim != 2:
        raise ValueError("mask must be a 2D array")

    flat = array.reshape(-1, order="C")
    counts: list[int] = []
    previous = 0
    run = 0
    for value in flat:
        current = int(value != 0)
        if current == previous:
            run += 1
            continue
        counts.append(run)
        run = 1
        previous = current
    counts.append(run)

    return {"size": [int(array.shape[0]), int(array.shape[1])], "counts": counts}


def bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if xs.size == 0 or ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
