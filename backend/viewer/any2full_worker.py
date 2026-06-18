from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persistent Any2Full worker")
    parser.add_argument("--any2full-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--encoder", default="vitl", choices=("vits", "vitb", "vitl"))
    return parser.parse_args()


def normalize_depth_array(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32)
    if arr.ndim == 4:
        return arr[0, 0]
    if arr.ndim == 3:
        return arr[0] if arr.shape[0] in (1, 3) else arr[:, :, 0]
    return arr


def main() -> None:
    args = parse_args()
    any2full_root = args.any2full_root.resolve()
    sys.path.insert(0, str(any2full_root))

    protocol_out = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from PIL import Image
        from torchvision import transforms as T
        from model.ours.any2full import Any2Full

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model_args = SimpleNamespace(
            checkpoint=str(args.checkpoint),
            encoder=args.encoder,
            da_ckpt_path=None,
            init_scailing=True,
            stage=1,
            max_depth=1e3,
            min_depth=1e-6,
        )
        model = Any2Full(encoder=args.encoder, da_ckpt_path=None, args=model_args)
        checkpoint = torch.load(str(args.checkpoint), map_location=device, weights_only=False)
        state = checkpoint.get("state_dict", checkpoint)
        cleaned = OrderedDict((k.replace("module.", ""), v) for k, v in state.items())
        model.load_state_dict(cleaned, strict=True)
        model = model.to(device).eval()
        rgb_transform = T.Compose(
            [
                T.ToTensor(),
                T.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
            ]
        )
        depth_transform = T.ToTensor()

    print(json.dumps({"ready": True, "device": device}), file=protocol_out, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            rgb_path = Path(request["rgb"])
            depth_path = Path(request["depth"])
            out_path = Path(request["out"])

            with contextlib.redirect_stdout(sys.stderr):
                rgb = rgb_transform(Image.open(rgb_path).convert("RGB")).unsqueeze(0).to(device)
                depth_arr = normalize_depth_array(np.load(depth_path))
                dep = depth_transform(Image.fromarray(depth_arr)).unsqueeze(0).to(device)
                with torch.no_grad():
                    pred = model({"rgb": rgb, "dep": dep})["pred"].squeeze(0).squeeze(0).cpu().numpy()

            out_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(out_path, pred.astype(np.float32))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "out": str(out_path),
                        "shape": list(pred.shape),
                        "min": float(np.nanmin(pred)),
                        "max": float(np.nanmax(pred)),
                    }
                ),
                file=protocol_out,
                flush=True,
            )
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), file=protocol_out, flush=True)


if __name__ == "__main__":
    main()
