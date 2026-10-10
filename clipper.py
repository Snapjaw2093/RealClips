"""RealClips: turn one long real estate video into vertical Shorts clips.

Usage:
    python clipper.py my_video.mp4

This file is the "director": it holds the settings and runs each step in order.
The real work lives in transcribe.py, select_clips.py and (soon) render.py.
"""

import shutil
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# SETTINGS - change these to taste
# ----------------------------------------------------------------------------
NUM_CLIPS = 3            # how many clips to make
MIN_CLIP_SECONDS = 30    # shortest allowed clip
MAX_CLIP_SECONDS = 60    # longest allowed clip
OUTPUT_DIR = Path("output")
# ----------------------------------------------------------------------------


def check_setup(video_path):
    """Check everything we need BEFORE doing slow work, with friendly errors."""
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "Error: FFmpeg is not installed (or not on your PATH).\n"
            "  Mac:     brew install ffmpeg\n"
            "  Windows: winget install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg"
        )
    if not video_path.is_file():
        sys.exit(f"Error: video file not found: {video_path}")


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python clipper.py my_video.mp4")
    video = Path(sys.argv[1])
    check_setup(video)

    # Imported here so the settings above load (and --help style errors show)
    # before the heavier libraries start up.
    from select_clips import ClipSelectionError, get_client, select_clips
    from transcribe import load_or_transcribe

    # Fail fast on a missing API key, before spending minutes transcribing.
    try:
        client = get_client()
    except ClipSelectionError as e:
        sys.exit(f"Error: {e}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Step 1: transcribe (saved to output/transcript.json so re-runs are fast).
    transcript = load_or_transcribe(video, OUTPUT_DIR / "transcript.json")
    # Clips can't overlap, so we need room for all of them back to back.
    needed = NUM_CLIPS * MIN_CLIP_SECONDS
    if transcript["duration"] < needed:
        sys.exit(
            f"Error: the video is only {transcript['duration']:.0f}s long, but "
            f"{NUM_CLIPS} clips of at least {MIN_CLIP_SECONDS}s need {needed}s. "
            "Use a longer video or lower NUM_CLIPS / MIN_CLIP_SECONDS at the top of clipper.py."
        )

    # Step 2: ask Claude for the best moments.
    try:
        clips = select_clips(
            transcript, NUM_CLIPS, MIN_CLIP_SECONDS, MAX_CLIP_SECONDS, client=client
        )
    except ClipSelectionError as e:
        sys.exit(f"Error: {e}")

    # Step 3: cut, crop to vertical, burn in captions, and write clips.json.
    from render import render_clips

    try:
        results = render_clips(video, transcript, clips, OUTPUT_DIR)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")

    print(f"\nDone! {len(results)} clips saved in {OUTPUT_DIR}/")
    for r in results:
        print(f"  {r['file']}  ({r['duration']:.0f}s)  {r['title']}")


if __name__ == "__main__":
    main()
