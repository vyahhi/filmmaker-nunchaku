#!/usr/bin/env python3
"""
filmmaker.py — generate a short film from a concept using Claude CLI + Nunchaku API

Usage:
  python filmmaker.py "a lonely astronaut finds an alien cat"
  python filmmaker.py          # Claude invents a concept
"""

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import wave
from dotenv import load_dotenv

load_dotenv()

NUNCHAKU_API_KEY = os.environ.get("NUNCHAKU_API_KEY")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/vyahhi/.claude/local/claude")
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
NUNCHAKU_BASE = "https://api.nunchaku.dev"
KOKORO_MODEL = os.environ.get("KOKORO_MODEL", str(Path(__file__).parent / "models/kokoro-v1.0.onnx"))
KOKORO_VOICES = os.environ.get("KOKORO_VOICES", str(Path(__file__).parent / "models/voices-v1.0.bin"))
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_sarah")


# ── helpers ──────────────────────────────────────────────────────────────────

def check_deps():
    if not NUNCHAKU_API_KEY:
        print("Error: NUNCHAKU_API_KEY not set. Export it before running.")
        sys.exit(1)
    r = subprocess.run([CLAUDE_BIN, "--version"], capture_output=True)
    if r.returncode != 0:
        print("Error: 'claude' CLI not found. Install Claude Code first.")
        sys.exit(1)
    r = subprocess.run([FFMPEG_BIN, "-version"], capture_output=True)
    if r.returncode != 0:
        print(f"Warning: ffmpeg not found at {FFMPEG_BIN}. Final stitch will be skipped.")


def call_claude(prompt: str) -> str:
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Claude error:\n{result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def nunchaku_headers() -> dict:
    return {
        "Authorization": f"Bearer {NUNCHAKU_API_KEY}",
        "Content-Type": "application/json",
    }


def retry(fn, retries: int = 3):
    """Run fn() up to `retries` times, handling 429 / 504 / timeouts. Sequential only."""
    for attempt in range(retries):
        try:
            resp = fn()
        except requests.exceptions.Timeout:
            print(f"  Request timed out — retry {attempt + 1}/{retries} …")
            time.sleep(5)
            continue
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limited — waiting {wait}s …")
            time.sleep(wait)
            continue
        if resp.status_code in (504, 524):
            print(f"  Gateway timeout ({resp.status_code}) — retry {attempt + 1}/{retries} …")
            time.sleep(10)
            continue
        if resp.status_code == 402:
            print("Error: out of Nunchaku credits. Check https://sundai.nunchaku.dev/")
            sys.exit(1)
        if not resp.ok:
            print(f"Error {resp.status_code}: {resp.text}")
            sys.exit(1)
        return resp
    print(f"Error: failed after {retries} retries.")
    sys.exit(1)


# ── planning ─────────────────────────────────────────────────────────────────

PLAN_SCHEMA = """
{
  "title": "film title",
  "style": "visual style, e.g. cinematic warm tones Studio Ghibli-inspired",
  "characters": [
    {"name": "name", "description": "detailed visual: hair clothing expression art-style full-body"}
  ],
  "scenes": [
    {
      "index": 1,
      "character": "must match characters[].name exactly",
      "image_prompt": "scene composition setting lighting — do NOT repeat character description",
      "video_prompt": "motion description for animating this scene",
      "narration": "subtitle text fitting ~5 seconds"
    }
  ]
}
"""

PLAN_RULES = """
Rules:
- Exactly 5 scenes
- Maximum 2 unique characters total
- Exactly 1 character per scene
- At least 1 character appears in 3+ scenes
- style is prepended to every image prompt automatically — do not put it in image_prompt
- image_prompt must NOT repeat character description
- Output ONLY the JSON object. No markdown fences, no explanation.
"""


def generate_plan(concept: str) -> dict:
    if concept:
        intro = f'You are a creative film director. Write a short film plan for: "{concept}"'
    else:
        intro = (
            "You are a creative film director. Invent a surprising, unexpected, "
            "surreal concept and write a short film plan for it."
        )

    prompt = f"{intro}\n\nOutput ONLY valid JSON matching this schema:\n{PLAN_SCHEMA}\n{PLAN_RULES}"
    print("Generating film plan via Claude …")
    raw = call_claude(prompt)

    # Strip accidental markdown fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Error: Claude returned invalid JSON.\n{e}\nRaw output:\n{raw[:500]}")
        sys.exit(1)


# ── nunchaku calls (all sequential — 1 concurrent thread) ────────────────────

def gen_portrait(character: dict, style: str, out_path: Path):
    prompt = f"{style}, {character['description']}, full body portrait"
    print(f"  Portrait → {character['name']} …")

    def call():
        return requests.post(
            f"{NUNCHAKU_BASE}/v1/images/generations",
            headers=nunchaku_headers(),
            json={
                "model": "nunchaku-flux.2-klein-9b",
                "prompt": prompt,
                "n": 1,
                "size": "1280x720",
                "tier": "fast",
                "response_format": "b64_json",
            },
            timeout=240,
        )

    resp = retry(call)
    out_path.write_bytes(base64.b64decode(resp.json()["data"][0]["b64_json"]))
    print(f"    saved {out_path}")


def gen_scene_image(scene: dict, style: str, portrait: Path, out_path: Path):
    img_b64 = base64.b64encode(portrait.read_bytes()).decode()
    prompt = f"{style}, {scene['image_prompt']}"
    print(f"  Scene {scene['index']} image …")

    def call():
        return requests.post(
            f"{NUNCHAKU_BASE}/v1/images/edits",
            headers=nunchaku_headers(),
            json={
                "model": "nunchaku-flux.2-klein-9b-edit",
                "prompt": prompt,
                "url": f"data:image/jpeg;base64,{img_b64}",
                "n": 1,
                "size": "1280x720",
                "tier": "fast",
                "response_format": "b64_json",
            },
            timeout=240,
        )

    resp = retry(call)
    out_path.write_bytes(base64.b64decode(resp.json()["data"][0]["b64_json"]))
    print(f"    saved {out_path}")


def gen_scene_video(scene: dict, scene_img: Path, out_path: Path):
    img_b64 = base64.b64encode(scene_img.read_bytes()).decode()
    data_uri = f"data:image/jpeg;base64,{img_b64}"
    prompt = scene["video_prompt"]
    print(f"  Scene {scene['index']} video (~30s) …")

    def call():
        return requests.post(
            f"{NUNCHAKU_BASE}/v1/video/animations",
            headers=nunchaku_headers(),
            json={
                "model": "nunchaku-wan2.2-lightning-i2v",
                "prompt": prompt,
                "n": 1,
                "size": "1280x720",
                "num_frames": 81,
                "num_inference_steps": 20,
                "guidance_scale": 1.0,
                "response_format": "b64_json",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            },
            timeout=300,
        )

    resp = retry(call)
    out_path.write_bytes(base64.b64decode(resp.json()["data"][0]["b64_json"]))
    print(f"    saved {out_path}")


# ── tts ──────────────────────────────────────────────────────────────────────

_kokoro = None

def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
    return _kokoro


def _save_wav(audio, sample_rate: int, out_path: Path):
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(audio_int16.tobytes())


NARRATION_PAD_START = 0.2  # seconds of silence before narration
NARRATION_PAD_END = 0.2    # seconds of silence after narration


def gen_narration(text: str, out_path: Path, speed: float = 1.0):
    kokoro = _get_kokoro()
    speed = max(0.5, min(speed, 2.0))
    audio, sample_rate = kokoro.create(text, voice=KOKORO_VOICE, speed=speed, lang="en-us")
    pad_start = np.zeros(int(sample_rate * NARRATION_PAD_START), dtype=np.float32)
    pad_end = np.zeros(int(sample_rate * NARRATION_PAD_END), dtype=np.float32)
    audio = np.concatenate([pad_start, audio, pad_end])
    _save_wav(audio, sample_rate, out_path)
    return len(audio) / sample_rate


def _audio_duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def slow_video(video: Path, factor: float, out_path: Path) -> bool:
    # factor > 1 = slower; re-encodes since setpts changes frame timing
    r = subprocess.run(
        [FFMPEG_BIN, "-y", "-i", str(video),
         "-vf", f"setpts={factor:.6f}*PTS",
         "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
         str(out_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  slow_video failed: {r.stderr[-300:]}")
        return False
    return True


def mix_audio(video: Path, audio: Path, out_path: Path):
    r = subprocess.run(
        [FFMPEG_BIN, "-y",
         "-i", str(video), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
         "-shortest", str(out_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  audio mix failed: {r.stderr[-300:]}")
        return False
    return True


# ── post-production ───────────────────────────────────────────────────────────

def clip_duration(clip: Path) -> float:
    r = subprocess.run(
        [FFPROBE_BIN,
         "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def write_srt(scenes: list, clip_paths: list[Path], out_path: Path):
    def ts(secs: float) -> str:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = int(secs % 60)
        ms = int(round((secs - int(secs)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    cursor = 0.0
    blocks = []
    for i, (scene, clip) in enumerate(zip(scenes, clip_paths), 1):
        dur = clip_duration(clip)
        blocks.append(f"{i}\n{ts(cursor)} --> {ts(cursor + dur - CROSSFADE_DURATION)}\n{scene['narration']}\n")
        cursor += dur - CROSSFADE_DURATION  # clips overlap during crossfade

    out_path.write_text("\n".join(blocks))
    print(f"  saved {out_path}")


CROSSFADE_DURATION = 0.5  # seconds


def stitch(clip_paths: list, out_path: Path, concat_file: Path) -> bool:
    if len(clip_paths) == 1:
        import shutil
        shutil.copy(clip_paths[0], out_path)
        return True

    n = len(clip_paths)
    cf = CROSSFADE_DURATION

    # Build filter_complex for video xfade + audio acrossfade
    inputs = " ".join(f"-i {p.resolve()}" for p in clip_paths)
    durations = [clip_duration(p) for p in clip_paths]

    # xfade offsets: each transition starts at cumulative_duration - cf
    video_filters = []
    audio_filters = []
    v_prev, a_prev = "[0:v]", "[0:a]"
    cumulative = durations[0]

    for i in range(1, n):
        v_out = f"[v{i}]" if i < n - 1 else "[vout]"
        a_out = f"[a{i}]" if i < n - 1 else "[aout]"
        offset = max(cumulative - cf, 0)
        video_filters.append(
            f"{v_prev}[{i}:v]xfade=transition=fade:duration={cf}:offset={offset:.4f}{v_out}"
        )
        audio_filters.append(
            f"{a_prev}[{i}:a]acrossfade=d={cf}{a_out}"
        )
        v_prev, a_prev = v_out, a_out
        cumulative += durations[i] - cf

    filter_complex = ";".join(video_filters + audio_filters)

    cmd = [FFMPEG_BIN, "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p.resolve())]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ffmpeg crossfade failed: {r.stderr[-400:]}")
        return False
    return True


def burn_subtitles(video: Path, srt: Path, out_path: Path) -> bool:
    # Escape srt path for ffmpeg subtitles filter (colons must be \: on all platforms)
    srt_esc = str(srt.resolve()).replace("\\", "/").replace(":", "\\:")
    style = r"FontName=Arial,FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Shadow=1,Alignment=2,MarginV=20"
    r = subprocess.run(
        [FFMPEG_BIN, "-y", "-i", str(video),
         "-vf", f"subtitles=filename={srt_esc}:force_style='{style}'",
         str(out_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  subtitle burn failed: {r.stderr[-400:]}")
        return False
    return True


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    check_deps()

    concept = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    if concept:
        print(f"Concept: {concept}")
    else:
        print("No concept given — Claude will invent one.")

    plan = generate_plan(concept)
    print(f"\nTitle:      {plan['title']}")
    print(f"Style:      {plan['style']}")
    print(f"Characters: {[c['name'] for c in plan['characters']]}")
    print(f"Scenes:     {len(plan['scenes'])}")

    # Output directory
    safe = "".join(c if c.isalnum() or c in "-_ " else "" for c in plan["title"]).strip().replace(" ", "_")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("output") / f"{safe}_{stamp}"
    chars_dir = out_dir / "characters"
    scenes_dir = out_dir / "scenes"
    chars_dir.mkdir(parents=True)
    scenes_dir.mkdir(parents=True)
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=2))
    print(f"\nOutput folder: {out_dir}\n")

    # Character portraits
    print("=== Character portraits ===")
    portraits: dict[str, Path] = {}
    for char in plan["characters"]:
        safe_name = char["name"].lower().replace(" ", "_")
        path = chars_dir / f"{safe_name}.jpg"
        gen_portrait(char, plan["style"], path)
        portraits[char["name"]] = path

    # Scenes — strictly sequential
    print("\n=== Scenes ===")
    clip_paths: list[Path] = []
    for scene in sorted(plan["scenes"], key=lambda s: s["index"]):
        idx = scene["index"]
        portrait = portraits.get(scene["character"]) or next(iter(portraits.values()))
        scene_img = scenes_dir / f"scene_{idx:02d}.jpg"
        scene_vid = scenes_dir / f"scene_{idx:02d}.mp4"

        gen_scene_image(scene, plan["style"], portrait, scene_img)
        gen_scene_video(scene, scene_img, scene_vid)

        # Narration — generate at 1x, then meet audio+video in the middle
        narration_wav = scenes_dir / f"scene_{idx:02d}.wav"
        print(f"  Scene {idx} narration …")
        vid_dur = clip_duration(scene_vid)
        aud_dur = gen_narration(scene["narration"], narration_wav)

        mixed_vid = scene_vid
        if aud_dur > vid_dur:
            target = (aud_dur + vid_dur) / 2
            # Speed up audio to target
            audio_speed = min(aud_dur / target, 2.0)
            aud_dur = gen_narration(scene["narration"], narration_wav, speed=audio_speed)
            # Slow down video to target
            slowed = scenes_dir / f"scene_{idx:02d}_slow.mp4"
            if slow_video(scene_vid, target / vid_dur, slowed):
                mixed_vid = slowed
            print(f"    target={target:.2f}s audio={aud_dur:.2f}s video={clip_duration(mixed_vid):.2f}s")

        scene_vid_audio = scenes_dir / f"scene_{idx:02d}_audio.mp4"
        if mix_audio(mixed_vid, narration_wav, scene_vid_audio):
            print(f"    saved {scene_vid_audio}")
            clip_paths.append(scene_vid_audio)
        else:
            clip_paths.append(scene_vid)  # fallback: no audio

    # Subtitles
    print("\n=== Subtitles ===")
    srt_path = out_dir / "subtitles.srt"
    write_srt(plan["scenes"], clip_paths, srt_path)

    # Stitch
    print("\n=== Stitching ===")
    no_subs = out_dir / "video_no_subs.mp4"
    final = out_dir / "final.mp4"
    if stitch(clip_paths, no_subs, out_dir / "concat.txt"):
        print(f"  saved {no_subs}")
        print("\n=== Burning subtitles ===")
        if burn_subtitles(no_subs, srt_path, final):
            print(f"  saved {final}")
            print(f"\n✓ Done — {final}")
        else:
            print(f"\n✓ Done (no subtitles burned) — {no_subs}")
    else:
        print(f"\nDone (no stitch). Clips in {scenes_dir}")


if __name__ == "__main__":
    main()
