from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import quest3_rgbd_align_viewer as align


DEFAULT_DATA_DIR = Path("E:/test/rgbd-v11/rgbd_test")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a dependency-free WebGL viewer for Quest 3 RGB-D point clouds.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--frame", type=int, default=0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--min-depth", type=float, default=0.2)
    parser.add_argument("--max-depth", type=float, default=8.0)
    parser.add_argument("--max-points", type=int, default=60000)
    return parser.parse_args()


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Quest 3 RGB-D Point Cloud</title>
  <style>
    html, body { margin: 0; height: 100%; overflow: hidden; background: #10100f; color: #eee; font-family: Consolas, monospace; }
    #hud { position: fixed; left: 14px; top: 12px; padding: 10px 12px; background: rgba(0,0,0,.65); border: 1px solid rgba(255,255,255,.25); line-height: 1.45; }
    canvas { width: 100vw; height: 100vh; display: block; }
  </style>
</head>
<body>
<canvas id="gl"></canvas>
<div id="hud">
  <div><b>Quest 3 RGB-D point cloud</b></div>
  <div id="stats"></div>
  <div>drag: rotate | wheel: zoom | double click: reset</div>
</div>
<script>
const payload = __PAYLOAD__;
const canvas = document.getElementById("gl");
const gl = canvas.getContext("webgl", { antialias: false, preserveDrawingBuffer: true });
if (!gl) throw new Error("WebGL is not available");

const points = new Float32Array(payload.points);
const colors = new Float32Array(payload.colors);
document.getElementById("stats").textContent = `${payload.frame} | ${points.length / 3} points | RGB camera coordinates`;

const vs = `
attribute vec3 aPosition;
attribute vec3 aColor;
uniform mat4 uMvp;
uniform float uPointSize;
varying vec3 vColor;
void main() {
  gl_Position = uMvp * vec4(aPosition, 1.0);
  gl_PointSize = uPointSize;
  vColor = aColor;
}`;
const fs = `
precision mediump float;
varying vec3 vColor;
void main() {
  vec2 uv = gl_PointCoord - vec2(0.5);
  if (dot(uv, uv) > 0.25) discard;
  gl_FragColor = vec4(vColor, 1.0);
}`;

function compile(type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
  return shader;
}
const program = gl.createProgram();
gl.attachShader(program, compile(gl.VERTEX_SHADER, vs));
gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fs));
gl.linkProgram(program);
if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
gl.useProgram(program);

function bufferAttrib(name, data, size) {
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(program, name);
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
}
bufferAttrib("aPosition", points, 3);
bufferAttrib("aColor", colors, 3);
const uMvp = gl.getUniformLocation(program, "uMvp");
const uPointSize = gl.getUniformLocation(program, "uPointSize");

let yaw = -0.65, pitch = -0.32, distance = payload.radius * 2.0;
let dragging = false, lastX = 0, lastY = 0;

canvas.addEventListener("mousedown", e => { dragging = true; lastX = e.clientX; lastY = e.clientY; });
window.addEventListener("mouseup", () => dragging = false);
window.addEventListener("mousemove", e => {
  if (!dragging) return;
  yaw += (e.clientX - lastX) * 0.006;
  pitch += (e.clientY - lastY) * 0.006;
  pitch = Math.max(-1.45, Math.min(1.45, pitch));
  lastX = e.clientX; lastY = e.clientY;
});
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  distance *= Math.exp(e.deltaY * 0.001);
  distance = Math.max(payload.radius * 0.2, Math.min(payload.radius * 8.0, distance));
}, { passive: false });
canvas.addEventListener("dblclick", () => { yaw = -0.65; pitch = -0.32; distance = payload.radius * 2.0; });

function matMul(a, b) {
  const o = new Float32Array(16);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) {
    o[c * 4 + r] = a[0 * 4 + r] * b[c * 4 + 0] + a[1 * 4 + r] * b[c * 4 + 1] + a[2 * 4 + r] * b[c * 4 + 2] + a[3 * 4 + r] * b[c * 4 + 3];
  }
  return o;
}
function perspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return new Float32Array([f/aspect,0,0,0, 0,f,0,0, 0,0,(far+near)*nf,-1, 0,0,2*far*near*nf,0]);
}
function viewMatrix() {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  const eye = [distance * sy * cp, distance * sp, distance * cy * cp];
  const center = payload.center;
  const f = normalize([center[0]-eye[0], center[1]-eye[1], center[2]-eye[2]]);
  const s = normalize(cross(f, [0,1,0]));
  const u = cross(s, f);
  return new Float32Array([s[0],u[0],-f[0],0, s[1],u[1],-f[1],0, s[2],u[2],-f[2],0, -dot(s,eye),-dot(u,eye),dot(f,eye),1]);
}
function cross(a,b){return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]}
function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}
function normalize(v){const n=Math.hypot(v[0],v[1],v[2])||1; return [v[0]/n,v[1]/n,v[2]/n]}

function render() {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.floor(canvas.clientWidth * dpr), h = Math.floor(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; gl.viewport(0, 0, w, h); }
  gl.clearColor(0.06, 0.06, 0.055, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST);
  gl.uniformMatrix4fv(uMvp, false, matMul(perspective(55*Math.PI/180, w/h, 0.02, 50.0), viewMatrix()));
  gl.uniform1f(uPointSize, Math.max(1.5, 2.4 * dpr));
  gl.drawArrays(gl.POINTS, 0, points.length / 3);
  requestAnimationFrame(render);
}
render();
</script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    frames = align.discover_frames(args.data)
    frame_dir = frames[int(np.clip(args.frame, 0, len(frames) - 1))]
    frame = align.load_frame(frame_dir, args.min_depth, args.max_depth, "sdk_reprojection", "raw")
    points = frame.cloud_points
    colors = frame.cloud_colors.astype(np.float32) / 255.0
    if points.shape[0] > args.max_points:
        indices = np.linspace(0, points.shape[0] - 1, args.max_points).astype(np.int32)
        points = points[indices]
        colors = colors[indices]

    center = np.median(points, axis=0)
    radius = float(np.percentile(np.linalg.norm(points - center, axis=1), 95))
    payload = {
        "frame": frame_dir.name,
        "points": points.astype(np.float32).reshape(-1).round(5).tolist(),
        "colors": colors.astype(np.float32).reshape(-1).round(4).tolist(),
        "center": center.astype(np.float32).round(5).tolist(),
        "radius": max(radius, 0.1),
    }
    output = args.output or (args.data / f"{frame_dir.name}_pointcloud_viewer.html")
    output.write_text(HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload)), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
