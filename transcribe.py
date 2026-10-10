"""Step 1: the "stenographer".

Listens to a video and writes down every word with its start/end time.
Uses faster-whisper, which runs on your own computer (no API key needed).

You can run this file by itself to test it:
    python transcribe.py my_video.mp4
"""

import json
import sys
from pathlib import Path

# Which Whisper model to use. Bigger = more accurate but slower + bigger download.
# Options: "tiny", "base", "small", "medium". "base" is a good start (~150 MB).
WHISPER_MODEL = "base"


def transcribe(video_path, model_size=WHISPER_MODEL):
    """Return {"duration": seconds, "words": [{"word", "start", "end"}, ...]}."""
    # Imported here (not at the top) so other files can import this module
    # quickly, and so we only pay the load cost when we actually transcribe.
    from faster_whisper import WhisperModel

    print(f"Loading Whisper model '{model_size}' (first run downloads it)...")
    # device="cpu" + int8 keeps it simple and works on any computer.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print("Transcribing... (this can take a while for long videos)")
    # word_timestamps=True is the key setting: it gives us a time for every word.
    # FFmpeg-style decoding is built in, so we can pass the video file directly.
    segments, info = model.transcribe(str(video_path), word_timestamps=True)

    words = []
    # `segments` is lazy: the actual work happens as we loop over it.
    for segment in segments:
        for w in segment.words:
            words.append(
                {
                    "word": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                }
            )

    return {"duration": round(info.duration, 2), "words": words}


def load_or_transcribe(video_path, cache_path):
    """Transcribe once, then reuse the saved result on later runs.

    Transcribing is the slowest step, so we save it to a JSON file. Delete
    that file if you want to force a fresh transcription.
    """
    cache_path = Path(cache_path)
    if cache_path.exists():
        print(f"Using saved transcript: {cache_path}")
        return json.loads(cache_path.read_text())

    result = transcribe(video_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python transcribe.py my_video.mp4")
    video = Path(sys.argv[1])
    if not video.exists():
        sys.exit(f"Error: video file not found: {video}")

    result = transcribe(video)
    print(f"\nVideo length: {result['duration']} seconds")
    print(f"Words found:  {len(result['words'])}")
    print("First 15 words:")
    for w in result["words"][:15]:
        print(f"  {w['start']:6.2f} - {w['end']:6.2f}  {w['word']}")
