from __future__ import annotations

import argparse
import asyncio
import json
import struct
import time
from dataclasses import dataclass
from pathlib import Path

import websockets


@dataclass
class RgbFrame:
    frame_id: int
    timestamp_ms: int
    width: int
    height: int
    jpeg: bytes


@dataclass
class DepthFrame:
    frame_id: int
    timestamp_ms: int
    width: int
    height: int
    row_stride: int
    pixel_stride: int
    payload: bytes


def parse_rgb_packet(packet: bytes) -> RgbFrame | None:
    if len(packet) < 28:
        return None
    try:
        magic, frame_id, timestamp_ms, width, height, payload_len = struct.unpack_from(
            "<4sI q I I I", packet, 0
        )
    except struct.error:
        return None

    if magic != b"RGB1" or payload_len <= 0:
        return None
    if len(packet) < 28 + payload_len:
        return None

    payload = packet[28 : 28 + payload_len]
    return RgbFrame(frame_id, timestamp_ms, width, height, payload)


def parse_depth_packet(packet: bytes) -> DepthFrame | None:
    if len(packet) < 36:
        return None
    try:
        (
            magic,
            frame_id,
            timestamp_ms,
            width,
            height,
            row_stride,
            pixel_stride,
            payload_len,
        ) = struct.unpack_from("<4sI q I I I I I", packet, 0)
    except struct.error:
        return None

    if magic != b"DEP1" or payload_len <= 0:
        return None
    if len(packet) < 36 + payload_len:
        return None

    payload = packet[36 : 36 + payload_len]
    return DepthFrame(
        frame_id, timestamp_ms, width, height, row_stride, pixel_stride, payload
    )


class PairRecorder:
    def __init__(
        self, out_dir: Path, tolerance_ms: int, max_pairs: int, duration_s: int
    ):
        self.out_dir = out_dir
        self.tolerance_ms = max(0, tolerance_ms)
        self.max_pairs = max_pairs
        self.duration_s = duration_s

        self.rgb_by_ts: dict[int, RgbFrame] = {}
        self.depth_by_ts: dict[int, DepthFrame] = {}

        self.pairs_saved = 0
        self.rgb_received = 0
        self.depth_received = 0

        self.start_time = time.time()

    def _evict_old(self) -> None:
        if len(self.rgb_by_ts) > 600:
            for k in sorted(self.rgb_by_ts.keys())[:200]:
                self.rgb_by_ts.pop(k, None)
        if len(self.depth_by_ts) > 600:
            for k in sorted(self.depth_by_ts.keys())[:200]:
                self.depth_by_ts.pop(k, None)

    def add_rgb(self, frame: RgbFrame) -> None:
        self.rgb_received += 1
        self.rgb_by_ts[frame.timestamp_ms] = frame
        self._try_pair(frame.timestamp_ms)
        self._evict_old()

    def add_depth(self, frame: DepthFrame) -> None:
        self.depth_received += 1
        self.depth_by_ts[frame.timestamp_ms] = frame
        self._try_pair(frame.timestamp_ms)
        self._evict_old()

    def _find_closest_depth_ts(self, rgb_ts: int) -> int | None:
        if not self.depth_by_ts:
            return None
        best_ts = None
        best_diff = None
        for ts in self.depth_by_ts:
            diff = abs(ts - rgb_ts)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_ts = ts
        if best_diff is None:
            return None
        if best_diff > self.tolerance_ms:
            return None
        return best_ts

    def _try_pair(self, ts: int) -> None:
        if self.max_pairs > 0 and self.pairs_saved >= self.max_pairs:
            return

        if ts in self.rgb_by_ts and ts in self.depth_by_ts:
            self._save_pair(
                self.rgb_by_ts.pop(ts), self.depth_by_ts.pop(ts), matched_ts=ts
            )
            return

        if ts in self.rgb_by_ts:
            depth_ts = self._find_closest_depth_ts(ts)
            if depth_ts is not None:
                self._save_pair(
                    self.rgb_by_ts.pop(ts),
                    self.depth_by_ts.pop(depth_ts),
                    matched_ts=depth_ts,
                )
            return

        if ts in self.depth_by_ts:
            rgb_ts = None
            best_diff = None
            for candidate_ts in self.rgb_by_ts:
                diff = abs(candidate_ts - ts)
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    rgb_ts = candidate_ts
            if (
                rgb_ts is not None
                and best_diff is not None
                and best_diff <= self.tolerance_ms
            ):
                self._save_pair(
                    self.rgb_by_ts.pop(rgb_ts),
                    self.depth_by_ts.pop(ts),
                    matched_ts=ts,
                )

    def _save_pair(self, rgb: RgbFrame, depth: DepthFrame, matched_ts: int) -> None:
        self.pairs_saved += 1
        pair_dir = self.out_dir / f"pair_{self.pairs_saved:06d}"
        pair_dir.mkdir(parents=True, exist_ok=True)

        (pair_dir / "rgb.jpg").write_bytes(rgb.jpeg)
        (pair_dir / "depth.f32").write_bytes(depth.payload)

        metadata = {
            "pair_index": self.pairs_saved,
            "matched_timestamp_ms": matched_ts,
            "timestamp_delta_ms": rgb.timestamp_ms - depth.timestamp_ms,
            "rgb": {
                "frame_id": rgb.frame_id,
                "timestamp_ms": rgb.timestamp_ms,
                "width": rgb.width,
                "height": rgb.height,
            },
            "depth": {
                "frame_id": depth.frame_id,
                "timestamp_ms": depth.timestamp_ms,
                "width": depth.width,
                "height": depth.height,
                "row_stride": depth.row_stride,
                "pixel_stride": depth.pixel_stride,
                "payload_bytes": len(depth.payload),
            },
        }
        (pair_dir / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8"
        )

        if self.pairs_saved % 20 == 0:
            elapsed = time.time() - self.start_time
            print(
                f"saved={self.pairs_saved} rgb_rx={self.rgb_received} depth_rx={self.depth_received} elapsed={elapsed:.1f}s"
            )

    def done(self) -> bool:
        if self.max_pairs > 0 and self.pairs_saved >= self.max_pairs:
            return True
        if self.duration_s > 0 and (time.time() - self.start_time) >= self.duration_s:
            return True
        return False


async def recv_rgb(uri: str, recorder: PairRecorder) -> None:
    async with websockets.connect(uri, max_size=16 * 1024 * 1024) as ws:
        while not recorder.done():
            msg = await ws.recv()
            if not isinstance(msg, (bytes, bytearray)):
                continue
            frame = parse_rgb_packet(bytes(msg))
            if frame is None:
                continue
            recorder.add_rgb(frame)


async def recv_depth(uri: str, recorder: PairRecorder) -> None:
    async with websockets.connect(uri, max_size=32 * 1024 * 1024) as ws:
        while not recorder.done():
            msg = await ws.recv()
            if not isinstance(msg, (bytes, bytearray)):
                continue
            frame = parse_depth_packet(bytes(msg))
            if frame is None:
                continue
            recorder.add_depth(frame)


async def run_capture(args) -> None:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    recorder = PairRecorder(
        out_dir=out_dir,
        tolerance_ms=args.tolerance_ms,
        max_pairs=args.max_pairs,
        duration_s=args.duration,
    )

    rgb_uri = f"ws://{args.host}:{args.port}/ws/rgb-preview-raw"
    depth_uri = f"ws://{args.host}:{args.port}/ws/depth-preview"

    print("capture start")
    print(f"rgb uri   : {rgb_uri}")
    print(f"depth uri : {depth_uri}")
    print(f"out dir   : {out_dir}")
    print(
        f"limits    : max_pairs={args.max_pairs} duration={args.duration}s tolerance={args.tolerance_ms}ms"
    )

    tasks = [
        asyncio.create_task(recv_rgb(rgb_uri, recorder)),
        asyncio.create_task(recv_depth(depth_uri, recorder)),
    ]

    try:
        while not recorder.done():
            await asyncio.sleep(0.2)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    summary = {
        "pairs_saved": recorder.pairs_saved,
        "rgb_received": recorder.rgb_received,
        "depth_received": recorder.depth_received,
        "duration_s": round(time.time() - recorder.start_time, 3),
        "tolerance_ms": args.tolerance_ms,
    }
    (out_dir / "capture_summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8"
    )

    print("capture done")
    print(json.dumps(summary, ensure_ascii=True, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record timestamp-paired RGB/Depth frames for offline calibration."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--out", default="calib_capture")
    parser.add_argument("--duration", type=int, default=90)
    parser.add_argument("--max-pairs", type=int, default=300)
    parser.add_argument("--tolerance-ms", type=int, default=0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run_capture(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
