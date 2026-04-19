# filmmaker-nunchaku

Generate a short film (~17s MP4) from a one-line concept.

**Pipeline:** Claude CLI writes the plan → Nunchaku generates images & videos → ffmpeg stitches the final film.

## Requirements

- [Claude Code](https://claude.ai/code) CLI (`claude` in PATH)
- Python 3.9+
- ffmpeg (`brew install ffmpeg`)
- A Nunchaku API key from [sundai.nunchaku.dev](https://sundai.nunchaku.dev/)

## Setup

```bash
pip install -r requirements.txt
export NUNCHAKU_API_KEY=sk-nunchaku-...
```

## Usage

```bash
# With a concept
python filmmaker.py "a lonely astronaut finds an alien cat"

# No concept — Claude invents one
python filmmaker.py
```

Output lands in `output/<title>_<timestamp>/`:

```
plan.json          # Claude's structured film plan
characters/        # one portrait per character
scenes/            # scene_01.jpg, scene_01.mp4, …
subtitles.srt
concat.txt
final.mp4
```

## Cost estimate

~$0.14 per film (5 scenes × image-to-video $0.025 + portraits ~$0.01).

## How it works

1. **Plan** — `claude -p` generates a JSON plan: title, style, characters, scene prompts
2. **Portraits** — one Nunchaku text→image call per character (reference image)
3. **Scene images** — Nunchaku image→image edit from character portrait (ensures consistency)
4. **Scene videos** — Nunchaku image→video per scene (~30s each, sequential)
5. **SRT** — written from plan narration fields
6. **Stitch** — ffmpeg concatenates clips into `final.mp4`

All Nunchaku calls are strictly sequential (no concurrency).
