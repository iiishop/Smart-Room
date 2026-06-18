# Viewer as RGB-D Alignment Server — Implementation Plan

> **For Hermes:** Dispatch to OpenCode with `--model openai/gpt-5.4`, load `ponytail` skill, then execute this plan.
> OpenCode prompt: "Load skill ponytail, then execute this plan."

**Goal:** Make the existing viewer (`tools/quest3_rgbd_align_viewer.py`) accept HTTP POST trigger payloads on port 8500 — the same format Unity already sends to `/api/track/start-final-rgbd`. No FastAPI, no ML models, no Quest 3 changes. One process: Tkinter viewer + threaded HTTP server.

**Architecture:** Add `--server` flag. When set, start a `http.server.HTTPServer` in a daemon thread listening on port 8500. POST handler receives multipart body (rgb_jpeg + depth_raw + meta_json), runs alignment via existing `align_depth_to_rgb_sdk()`, updates the Tkinter display via `after()` (thread-safe).

**Tech Stack:** stdlib `http.server`, stdlib `threading`, stdlib `cgi`/`email` for multipart parsing. No new deps. Existing viewer alignment code reused as-is.

---

## Task 1: Add `--server` CLI flag

**Objective:** Add a boolean flag so the viewer knows when to start the HTTP server.

**Files:**
- Modify: `tools/quest3_rgbd_align_viewer.py` — `parse_args()` (line 38)

**Step 1: Add argument**

```python
parser.add_argument("--server", action="store_true", help="Start HTTP server on port 8500 to accept trigger payloads from Quest 3.")
```

**Step 2: Verify**

```bash
uv run python -c "from tools.quest3_rgbd_align_viewer import parse_args; import sys; sys.argv=['test','--server','--no-ui']; args=parse_args(); assert args.server; print('OK')"
```

---

## Task 2: Create `load_frame_from_payload(rgb_jpeg_bytes, depth_raw_bytes, meta_json_str, min_depth, max_depth)` 

**Objective:** New function that does the same alignment as `load_frame()` but takes in-memory data instead of disk files.

**Files:**
- Modify: `tools/quest3_rgbd_align_viewer.py` — new function before `load_frame()` (~line 460)

**Step 1: Write function**

```python
def load_frame_from_payload(
    rgb_jpeg_bytes: bytes,
    depth_raw_bytes: bytes,
    meta_json_str: str,
    min_depth: float,
    max_depth: float,
) -> FrameData:
    """Load and align a frame from Quest 3 trigger payload (in-memory)."""
    import io
    meta = json.loads(meta_json_str)
    rgb = cv2.imdecode(np.frombuffer(rgb_jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)
    
    depth_meta = meta["depth"]
    depth_w = int(depth_meta["resolution_w"])
    depth_h = int(depth_meta["resolution_h"])
    depth_ndc = np.frombuffer(depth_raw_bytes, dtype=np.float32).reshape((depth_h, depth_w))
    
    depth_m = raw_depth_to_linear_m(depth_ndc, depth_meta)
    
    aligned, points_rgb_all, _ = align_depth_to_rgb_sdk(depth_m, meta, min_depth, max_depth)
    if aligned is None or not np.any(aligned > 0):
        print("  [WARN] alignment produced no valid depth pixels")
    
    overlay_rgb = make_depth_overlay(rgb, aligned, min_depth, max_depth)
    cloud_points, cloud_colors = sample_cloud_colors(rgb, points_rgb_all, meta["rgb"])
    
    return FrameData(
        frame_dir=Path("."),
        meta=meta, rgb=rgb,
        depth_ndc=depth_ndc, depth_m=depth_m,
        aligned_depth=aligned, overlay_rgb=overlay_rgb,
        any2full_depth=None, any2full_overlay_rgb=None, any2full_path=None,
        cloud_points=cloud_points, cloud_colors=cloud_colors,
        projected_depth_count=int(np.count_nonzero(aligned > 0)),
        any2full_depth_count=0, alignment_mode="sdk_reprojection",
    )
```

**Step 2: Verify import check**

```bash
uv run python -c "from tools.quest3_rgbd_align_viewer import load_frame_from_payload; print('OK')"
```

---

## Task 3: Add HTTP server thread

**Objective:** When `--server` is set, start a threaded HTTP server on port 8500 that accepts POST multipart payloads.

**Files:**
- Modify: `tools/quest3_rgbd_align_viewer.py` — new class and server start function

**Step 1: Write HTTP handler and server start**

Add before `RgbdViewer` class (~line 555):

```python
import http.server
import threading
import cgi
from io import BytesIO


class _PayloadHandler(http.server.BaseHTTPRequestHandler):
    viewer_ref: "RgbdViewer | None" = None
    
    def do_POST(self):
        if self.path != "/api/track/start-final-rgbd":
            self.send_response(404)
            self.end_headers()
            return
        
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_response(400)
            self.end_headers()
            self.write(b"expected multipart/form-data")
            return
        
        # Parse multipart
        boundary = content_type.split("boundary=")[-1].encode()
        body = self.rfile.read(int(self.headers["Content-Length"]))
        
        parts = _parse_multipart(body, boundary)
        rgb_jpeg = parts.get("rgb_jpeg")
        depth_raw = parts.get("depth_raw")
        meta_json = parts.get("meta_json")
        
        if not all([rgb_jpeg, depth_raw, meta_json]):
            self.send_response(400)
            self.end_headers()
            self.write(b"missing fields: need rgb_jpeg, depth_raw, meta_json")
            return
        
        viewer = _PayloadHandler.viewer_ref
        if viewer is None:
            self.send_response(503)
            self.end_headers()
            return
        
        try:
            meta_str = meta_json.decode("utf-8") if isinstance(meta_json, bytes) else meta_json
            frame = load_frame_from_payload(
                rgb_jpeg, depth_raw, meta_str,
                viewer.args.min_depth, viewer.args.max_depth,
            )
            viewer.root.after(0, lambda: viewer._on_network_frame(frame, rgb_jpeg))
        except Exception as exc:
            print(f"[server] alignment failed: {exc}")
            self.send_response(500)
            self.end_headers()
            self.write(str(exc).encode())
            return
        
        self.send_response(200)
        self.end_headers()
        self.write(b"ok")
    
    def log_message(self, format, *args):
        print(f"[server] {args[0]}")


def _parse_multipart(body: bytes, boundary: bytes) -> dict[str, bytes]:
    """Parse multipart form-data into {field_name: bytes}."""
    parts = {}
    sep = b"--" + boundary
    sections = body.split(sep)
    for section in sections:
        if b"Content-Disposition" not in section:
            continue
        header_end = section.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        header = section[:header_end].decode("utf-8", errors="replace")
        data = section[header_end + 4:]
        if data.endswith(b"\r\n"):
            data = data[:-2]
        
        for line in header.split("\r\n"):
            if "name=" in line:
                name = line.split('name="')[1].split('"')[0]
                if data.startswith(b"\r\n"):
                    data = data[2:]
                parts[name] = data
                break
    return parts


def _start_server(viewer: "RgbdViewer", port: int = 8500) -> threading.Thread:
    _PayloadHandler.viewer_ref = viewer
    server = http.server.HTTPServer(("0.0.0.0", port), _PayloadHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[server] listening on :{port}")
    return t
```

**Step 2: Add `_on_network_frame` to RgbdViewer**

```python
def _on_network_frame(self, frame: FrameData, rgb_jpeg: bytes) -> None:
    """Called from HTTP thread via after() — update display with new frame."""
    self.frame = frame
    self.rgb_scale = 1.0
    self._last_hover_display_xy = None
    self.nearest_x = None
    self.nearest_y = None
    self.nearest_dist = None
    self.current_preview_source = "sparse"
    self.update_display()
```

**Step 3: Wire `--server` in `main()`**

```python
def main() -> None:
    args = parse_args()
    frames = discover_frames(args.data)
    # ... existing export logic ...
    viewer = RgbdViewer(args, frames)
    if args.server:
        _start_server(viewer, port=8500)
    viewer.run()
```

**Step 4: Verify**

```bash
uv run python -c "from tools.quest3_rgbd_align_viewer import _start_server, _PayloadHandler, _parse_multipart; print('OK')"
```

---

## Task 4: Commit

```bash
git add tools/quest3_rgbd_align_viewer.py
git commit -m "feat(viewer): accept HTTP trigger payloads via --server flag on port 8500"
```
