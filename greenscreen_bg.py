"""Replace a green screen with an AI-generated background (Comfy Cloud + Flux Fill).

    python greenscreen_bg.py INPUT "background prompt" [-o OUTPUT] [--seed N]

INPUT can be a photo (.jpg/.png) or a video (.mp4/.mov/...).
Needs COMFY_CLOUD_API_KEY in the environment, plus ffmpeg on PATH for video.

How it works (same idea as the Midjourney editor trick):
  1. Key out the green from one frame to get a mask.
  2. Flux Fill paints a new scene around you, so its lighting fits your pose.
  3. For video, a second pass removes you from that scene to get a clean plate.
  4. Your original pixels are keyed and composited back on top of every frame.
"""
import argparse
import os
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
import requests

BASE = "https://cloud.comfy.org"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
GEN_PIXELS = 1024 * 1024  # Flux works best around 1 megapixel


def api_key():
    key = os.environ.get("COMFY_CLOUD_API_KEY")
    if not key and sys.platform == "win32":
        # A freshly-set Windows user variable isn't visible to already-open shells.
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = winreg.QueryValueEx(k, "COMFY_CLOUD_API_KEY")[0]
        except OSError:
            pass
    if not key:
        sys.exit("COMFY_CLOUD_API_KEY is not set.")
    return key


# ---------------------------------------------------------------- keying

def green_alpha(bgr, low=25, high=70):
    """Soft background alpha: 1 = green screen, 0 = subject."""
    b, g, r = [c.astype(np.int16) for c in cv2.split(bgr)]
    greenness = g - np.maximum(r, b)
    alpha = np.clip((greenness - low) / float(high - low), 0, 1).astype(np.float32)
    return cv2.GaussianBlur(alpha, (3, 3), 0)


def despill(bgr):
    """Pull green spill off the subject's edges and hair."""
    out = bgr.copy()
    limit = np.maximum(bgr[..., 0], bgr[..., 2])
    out[..., 1] = np.minimum(bgr[..., 1], limit)
    return out


def composite(fg_bgr, plate_bgr):
    a = green_alpha(fg_bgr)[..., None]
    fg = despill(fg_bgr).astype(np.float32)
    return (fg * (1 - a) + plate_bgr.astype(np.float32) * a).astype(np.uint8)


def subject_mask(bgr):
    """Hard subject mask, cleaned of specks."""
    m = (green_alpha(bgr) < 0.5).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, k)


# ---------------------------------------------------------------- comfy cloud

class Comfy:
    def __init__(self):
        self.h = {"X-API-Key": api_key()}

    def upload(self, png_bytes, name):
        r = requests.post(f"{BASE}/api/upload/image", headers=self.h,
                          files={"image": (name, png_bytes, "image/png")},
                          data={"type": "input", "overwrite": "true"})
        r.raise_for_status()
        return r.json()["name"]

    def run(self, workflow):
        r = requests.post(f"{BASE}/api/prompt", headers=self.h, json={"prompt": workflow})
        if r.status_code == 402:
            sys.exit("Comfy Cloud: out of credits.")
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        t0 = time.time()
        while True:
            s = requests.get(f"{BASE}/api/job/{pid}/status", headers=self.h).json()["status"]
            if s in ("success", "completed"):
                break
            if s in ("error", "non_retryable_error", "lost", "cancelled", "failed"):
                sys.exit(f"Comfy Cloud job {pid} ended with status: {s}")
            print(f"\r  {s} ({int(time.time() - t0)}s)", end="", flush=True)
            time.sleep(3)
        print(f"\r  done ({int(time.time() - t0)}s)      ")
        return self.first_image(pid)

    def first_image(self, pid):
        r = requests.get(f"{BASE}/api/history_v2/{pid}", headers=self.h)
        r.raise_for_status()
        data = r.json()
        entry = data.get(pid) or (data.get("history") or [data])[0]
        for out in entry["outputs"].values():
            for img in out.get("images", []):
                v = requests.get(f"{BASE}/api/view", headers=self.h, allow_redirects=False,
                                 params={"filename": img["filename"],
                                         "subfolder": img.get("subfolder", ""),
                                         "type": img.get("type", "output")})
                url = v.headers.get("Location")
                # Fetch the signed URL without our API key.
                content = requests.get(url).content if url else v.content
                return cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        sys.exit(f"No image output found for job {pid}")

    def flux_fill(self, bgr, fill_mask, prompt, seed):
        """Inpaint where fill_mask == 1. Image is uploaded as RGBA: transparent = fill."""
        rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        rgba[..., 3] = np.where(fill_mask > 0, 0, 255).astype(np.uint8)
        name = self.upload(cv2.imencode(".png", rgba)[1].tobytes(), f"gs_{uuid.uuid4().hex}.png")
        wf = {
            "1": {"class_type": "LoadImage", "inputs": {"image": name}},
            "2": {"class_type": "UNETLoader",
                  "inputs": {"unet_name": "flux1-fill-dev.safetensors", "weight_dtype": "default"}},
            "3": {"class_type": "DualCLIPLoader",
                  "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp16.safetensors",
                             "type": "flux"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
            "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["5", 0], "guidance": 30}},
            "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
            "8": {"class_type": "DifferentialDiffusion", "inputs": {"model": ["2", 0]}},
            "9": {"class_type": "InpaintModelConditioning",
                  "inputs": {"positive": ["6", 0], "negative": ["7", 0], "vae": ["4", 0],
                             "pixels": ["1", 0], "mask": ["1", 1], "noise_mask": True}},
            "10": {"class_type": "KSampler",
                   "inputs": {"model": ["8", 0], "positive": ["9", 0], "negative": ["9", 1],
                              "latent_image": ["9", 2], "seed": seed, "steps": 20, "cfg": 1.0,
                              "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
            "12": {"class_type": "SaveImage", "inputs": {"images": ["11", 0], "filename_prefix": "greenscreen"}},
        }
        return self.run(wf)


# ---------------------------------------------------------------- pipeline

def gen_size(w, h):
    s = (GEN_PIXELS / (w * h)) ** 0.5
    return max(16, round(w * s / 16) * 16), max(16, round(h * s / 16) * 16)


def read_video_frames(path, picks):
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in picks(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    return frames


def make_plate(comfy, key_frame, prompt, seed, clean, union_mask=None):
    H, W = key_frame.shape[:2]
    gw, gh = gen_size(W, H)
    small = cv2.resize(key_frame, (gw, gh), interpolation=cv2.INTER_AREA)
    subj = subject_mask(small)
    # Fill the green plus a few pixels into the subject edge so no green fringe survives.
    fill = cv2.dilate(1 - subj, np.ones((7, 7), np.uint8))

    print("Generating background around you...")
    plate = comfy.flux_fill(small, fill, prompt, seed)

    if clean:
        # Video: the subject moves, so the plate must not contain them. Paint them out too.
        m = union_mask if union_mask is not None else subj
        m = cv2.resize(m, (gw, gh), interpolation=cv2.INTER_NEAREST)
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
        print("Removing you from the background (clean plate for video)...")
        plate = comfy.flux_fill(plate, m, prompt + ". The scene is empty, no people.", seed + 1)

    return cv2.resize(plate, (W, H), interpolation=cv2.INTER_LANCZOS4)


def process_image(inp, out, prompt, seed):
    img = cv2.imread(str(inp))
    if img is None:
        sys.exit(f"Can't read image: {inp}")
    plate = make_plate(Comfy(), img, prompt, seed, clean=False)
    cv2.imwrite(str(out.with_name(out.stem + "_plate.png")), plate)
    cv2.imwrite(str(out), composite(img, plate))


def process_video(inp, out, prompt, seed):
    # Key frame from the middle; subject footprint from ~12 frames across the clip.
    key = read_video_frames(inp, lambda n: [n // 2])[0]
    samples = read_video_frames(inp, lambda n: np.linspace(0, n - 1, 12).astype(int))
    union = np.zeros(key.shape[:2], np.uint8)
    for f in samples:
        union |= subject_mask(f)

    plate = make_plate(Comfy(), key, prompt, seed, clean=True, union_mask=union)
    cv2.imwrite(str(out.with_name(out.stem + "_plate.png")), plate)

    cap = cv2.VideoCapture(str(inp))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
         "-i", str(inp), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        stdin=subprocess.PIPE)
    i = 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        ff.stdin.write(composite(f, plate).tobytes())
        i += 1
        print(f"\rCompositing frame {i}/{n}", end="", flush=True)
    print()
    cap.release()
    ff.stdin.close()
    ff.wait()


def check():
    ok = True
    r = requests.get(f"{BASE}/api/user", headers={"X-API-Key": api_key()})
    if r.status_code == 200:
        print("OK   Comfy Cloud API key works")
    else:
        ok = False
        print(f"FAIL Comfy Cloud returned HTTP {r.status_code} "
              "(401 = bad key, 429 = no active subscription)")
    import shutil
    if shutil.which("ffmpeg"):
        print("OK   ffmpeg found (needed for video only)")
    else:
        print("WARN ffmpeg not on PATH: photos will work, videos will not")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description="Replace a green screen with an AI background.")
    p.add_argument("input", type=Path, nargs="?", help="green screen photo or video")
    p.add_argument("prompt", nargs="?", help='e.g. "a podcast studio in a penthouse overlooking New York City"')
    p.add_argument("-o", "--output", type=Path, help="output file (default: INPUT_bg.png/.mp4)")
    p.add_argument("--seed", type=int, default=None, help="reuse a seed to reproduce a result")
    p.add_argument("--check", action="store_true", help="verify API key, Comfy Cloud access and ffmpeg, then exit")
    a = p.parse_args()

    if a.check:
        check()
        return
    if not a.input or not a.prompt:
        p.error("INPUT and PROMPT are required")
    if not a.input.is_file():
        sys.exit(f"Input not found: {a.input}")

    is_video = a.input.suffix.lower() in VIDEO_EXTS
    out = a.output or a.input.with_name(a.input.stem + ("_bg.mp4" if is_video else "_bg.png"))
    seed = a.seed if a.seed is not None else random.randint(0, 2**31)
    prompt = a.prompt + ", photorealistic photograph, natural lighting, shot on a cinema camera"
    print(f"Seed: {seed}")
    (process_video if is_video else process_image)(a.input, out, prompt, seed)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
