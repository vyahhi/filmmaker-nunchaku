# filmmaker-nunchaku

Generate a short film (~17s MP4) from a one-line concept.

**Pipeline:** Claude CLI writes the plan → Nunchaku generates images & videos → Kokoro TTS narrates each scene → ffmpeg stitches the final film with burned-in subtitles.

## Requirements

- [Claude Code](https://claude.ai/code) CLI (provides the `claude` command)
- Python 3.9+
- ffmpeg with **libass** (for burned-in subtitles — see below)
- Kokoro TTS model files (for narration — see below)
- A Nunchaku API key

## Installation

### 1. Clone and install Python deps

```bash
git clone https://github.com/vyahhi/filmmaker-nunchaku.git
cd filmmaker-nunchaku
pip install -r requirements.txt
```

### 2. Download Kokoro TTS model files

```bash
mkdir -p models && cd models
curl -LO https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx
curl -LO https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin
cd ..
```

### 3. Install ffmpeg with libass

The standard `brew install ffmpeg` does **not** include libass (required for burning subtitles into the video). Install the full build instead:

```bash
brew install ffmpeg-full
```

This installs to `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`. The script uses this path by default. Override with `FFMPEG_BIN` / `FFPROBE_BIN` env vars if your path differs.

> **Linux:** `apt install ffmpeg` usually includes libass. If subtitle burning fails, try `apt install ffmpeg libass-dev`.

### 4. Set your API key

```bash
cp .env.example .env
# edit .env and paste your NUNCHAKU_API_KEY
```

Or export directly:

```bash
export NUNCHAKU_API_KEY=sk-nunchaku-...
```

## Usage

```bash
# With a concept
python filmmaker.py "a lonely astronaut finds an alien cat"

# No concept — Claude invents a surreal one
python filmmaker.py
```

Output lands in `output/<title>_<timestamp>/`:

```
plan.json            # structured film plan (title, style, characters, scenes)
characters/          # one portrait per character
scenes/              # scene_01.jpg + scene_01.mp4 per scene
subtitles.srt        # timed subtitles matched to actual clip durations
concat.txt           # ffmpeg input list
video_no_subs.mp4    # stitched clips, no subtitles
final.mp4            # final film with subtitles burned in
```

## Cost estimate

Per film (approximate, based on current Nunchaku pricing):

| Step | Calls | Price each | Subtotal |
|------|-------|-----------|----------|
| Character portraits (text→image, `fast`) | ~2 | $0.0032 | ~$0.006 |
| Scene images (image→image edit, `fast`) | 5 | $0.004 | $0.020 |
| Scene videos (image→video, 20 steps) | 5 | ~$0.025+ | ~$0.125+ |
| **Total** | | | **~$0.15–0.20** |

Video cost may vary — pricing per step is not published.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NUNCHAKU_API_KEY` | — | Required |
| `CLAUDE_BIN` | `/Users/vyahhi/.claude/local/claude` | Path to `claude` CLI |
| `FFMPEG_BIN` | `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` | Path to ffmpeg with libass |
| `FFPROBE_BIN` | `/opt/homebrew/opt/ffmpeg-full/bin/ffprobe` | Path to ffprobe |
| `KOKORO_MODEL` | `models/kokoro-v1.0.onnx` | Path to Kokoro ONNX model |
| `KOKORO_VOICES` | `models/voices-v1.0.bin` | Path to Kokoro voices file |
| `KOKORO_VOICE` | `af_sarah` | Voice to use for narration |

## How it works

1. **Plan** — `claude -p` generates a JSON plan: title, visual style, characters, per-scene image/video/narration prompts
2. **Portraits** — one Nunchaku text→image call per character (reference portrait)
3. **Scene images** — Nunchaku image→image edit seeded from the character portrait (ensures visual consistency across scenes)
4. **Scene videos** — Nunchaku image→video per scene (~30s each, strictly sequential)
5. **Narration** — Kokoro TTS generates a WAV per scene from the narration text; mixed into the clip via ffmpeg
6. **Subtitles** — SRT written with timestamps derived from actual clip durations via ffprobe
7. **Stitch** — ffmpeg concatenates clips → `video_no_subs.mp4`
8. **Burn** — ffmpeg + libass renders subtitles into pixels → `final.mp4`

