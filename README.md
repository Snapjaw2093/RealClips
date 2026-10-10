# RealClips

Turn one long real estate video (listing walkthrough or market update) into
3 vertical 1080x1920 clips with burned-in captions, ready for YouTube Shorts.

```
python clipper.py my_video.mp4
```

## How it works

| Step | File | What it does |
|---|---|---|
| 1. Transcribe | `transcribe.py` | faster-whisper (runs locally) writes down every word with its time |
| 2. Pick clips | `select_clips.py` | Claude picks the best 30-60s moments; the code double-checks its answer |
| 3. Cut + caption | `render.py` | FFmpeg cuts each moment, crops to 9:16, burns in captions |
| Runs it all | `clipper.py` | Settings at the top, runs the steps in order |

## Setup

You need Python 3.10+ and FFmpeg.

1. **Install FFmpeg** (https://ffmpeg.org)
   - Mac: `brew install ffmpeg`
   - Windows: `winget install ffmpeg`
   - Ubuntu: `sudo apt install ffmpeg`
2. **Create a virtual environment and install packages**
   ```
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Add your Anthropic API key**
   ```
   cp .env.example .env
   ```
   Then open `.env` and paste your key after `ANTHROPIC_API_KEY=`
   (get one at https://console.anthropic.com/). `.env` is in `.gitignore`,
   so it never gets committed.

The first run downloads the Whisper model (about 150 MB).

## Run it

```
python clipper.py my_video.mp4
```

Results go in `output/`:

- `clip_1.mp4`, `clip_2.mp4`, `clip_3.mp4` - the finished vertical clips
- `clips.json` - title, start/end time, length and reason for each clip
- `transcript.json` - saved transcript. Re-runs reuse it, so they skip the
  slow step. Delete it if you switch to a different video.

## Settings

At the top of `clipper.py`:

```python
NUM_CLIPS = 3
MIN_CLIP_SECONDS = 30
MAX_CLIP_SECONDS = 60
```

Other easy tweaks:
- `transcribe.py`: `WHISPER_MODEL` (`"small"` is more accurate, slower)
- `select_clips.py`: `CLAUDE_MODEL`
- `render.py`: caption font, size, position, outline, words per caption

## Tests

No API key or video needed (Claude is faked):

```
python -m unittest test_select_clips test_render -v
```

## Common errors

| Message | Fix |
|---|---|
| FFmpeg is not installed | Install it (see Setup step 1) |
| ANTHROPIC_API_KEY is not set | Create `.env` (see Setup step 3) |
| the video is only Ns long | Use a longer video, or lower `NUM_CLIPS` / `MIN_CLIP_SECONDS` |
| no speech was found | The video has no audible talking to transcribe |

## Known limits (prototype)

- Reframing is a plain center crop. A speaker off to one side may be cut off.
- Captions use Arial. If it isn't installed, FFmpeg picks a similar font.

## Ideas for later

The code is split so each of these has an obvious home:

- Upload page and approve button (Vercel): call `clipper.py`'s steps from a web handler
- Smarter reframing that follows the speaker's face: replace the `crop` in `render.render_clip`
- Agent logo and branding: add an overlay in `render.render_clip`
- Auto-posting to YouTube Shorts: a new `post.py` that reads `clips.json`
