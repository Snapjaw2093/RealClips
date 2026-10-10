"""Step 3: the "projectionist".

For each chosen moment this file:
  1. cuts that piece out of the long video,
  2. crops it to vertical 9:16 (1080x1920),
  3. burns in captions built from the word timestamps.

Captions are made as an ".ass" subtitle file (a plain-text format that lets us
control font, size and position), which FFmpeg then paints onto the video.
"""

import json
import subprocess
from pathlib import Path

# ----------------------------------------------------------------------------
# LOOK & FEEL - easy to tweak
# ----------------------------------------------------------------------------
OUT_WIDTH, OUT_HEIGHT = 1080, 1920   # vertical 9:16, what YouTube Shorts wants
CAPTION_FONT = "Arial"               # falls back to a similar bold font if missing
CAPTION_FONT_SIZE = 96               # in output pixels (the video is 1920 tall)
CAPTION_BOTTOM_MARGIN = 600          # pixels up from the bottom edge -> lower-middle
CAPTION_OUTLINE = 7                  # thickness of the black outline
WORDS_PER_CAPTION = 3                # how many words show on screen at once
# ----------------------------------------------------------------------------


def clean_words(words):
    """Glue stray punctuation tokens onto the word before them.

    Whisper sometimes gives us pieces like "-minute" or ",000". On their own
    they would show up as separate captions with odd spacing, so we merge them
    with the previous word ("five" + "-minute" -> "five-minute").
    """
    cleaned = []
    for w in words:
        text = w["word"]
        if cleaned and text and text[0] in ",.-%'’":
            cleaned[-1] = {
                "word": cleaned[-1]["word"] + text,
                "start": cleaned[-1]["start"],
                "end": w["end"],
            }
        else:
            cleaned.append(dict(w))
    return cleaned


def group_words(words, per_caption=WORDS_PER_CAPTION):
    """Split words into small groups, each one a caption on screen.

    A group ends after `per_caption` words, or early at the end of a
    sentence so one caption never straddles two sentences.
    """
    groups, current = [], []
    for w in words:
        current.append(w)
        if len(current) >= per_caption or w["word"].endswith((".", "?", "!")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def ass_time(seconds):
    """Format seconds the way .ass files want: H:MM:SS.cc (centiseconds)."""
    cs = round(seconds * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(words, clip_start, clip_end):
    """Build the text of an .ass caption file for one clip.

    `words` is the full transcript; we keep only the words inside the clip and
    shift their times so 0 means "start of the clip".
    """
    in_clip = [w for w in words if w["start"] >= clip_start - 0.05 and w["end"] <= clip_end + 0.05]
    groups = group_words(clean_words(in_clip))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_WIDTH}
PlayResY: {OUT_HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{CAPTION_FONT},{CAPTION_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{CAPTION_OUTLINE},3,2,60,60,{CAPTION_BOTTOM_MARGIN},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for i, group in enumerate(groups):
        start = group[0]["start"] - clip_start
        end = group[-1]["end"] - clip_start
        # Keep the caption up until the next one begins (no blinking gaps),
        # unless the speaker pauses for more than half a second.
        if i + 1 < len(groups):
            next_start = groups[i + 1][0]["start"] - clip_start
            if next_start - end < 0.5:
                end = next_start
        start, end = max(start, 0), max(end, start + 0.1)
        # Curly braces mean "formatting code" in .ass, so remove them from text.
        text = " ".join(w["word"] for w in group).replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"


def ffmpeg_filter_path(path):
    """Escape a file path for use inside an FFmpeg filter string."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_clip(video_path, clip, ass_path, out_path):
    """Run FFmpeg once to cut, crop, and caption a single clip."""
    duration = clip["end"] - clip["start"]

    # The video filter chain, applied in order:
    #   crop  - cut a 9:16 window out of the middle of the frame
    #           (min() keeps it working if the source is already narrow)
    #   scale - resize that window to exactly 1080x1920
    #   ass   - paint the captions on top
    video_filter = (
        "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',"
        f"scale={OUT_WIDTH}:{OUT_HEIGHT},setsar=1,"
        f"ass='{ffmpeg_filter_path(ass_path)}'"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(clip["start"]),      # jump to the clip start (before -i = fast)
        "-i", str(video_path),
        "-t", f"{duration:.2f}",         # keep this many seconds
        "-vf", video_filter,
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",       # lets the video start playing before fully loaded
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed on {out_path.name}:\n{result.stderr.strip()}")


def render_clips(video_path, transcript, clips, output_dir):
    """Make every clip, then write clips.json. Returns the list written to it."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for n, clip in enumerate(clips, start=1):
        name = f"clip_{n}"
        ass_path = output_dir / f"{name}.ass"
        out_path = output_dir / f"{name}.mp4"
        print(f"Rendering {out_path.name}: {clip['title']} ({clip['start']}s - {clip['end']}s)")

        ass_path.write_text(build_ass(transcript["words"], clip["start"], clip["end"]), encoding="utf-8")
        render_clip(video_path, clip, ass_path, out_path)
        ass_path.unlink()  # the captions are burned in now; the helper file isn't needed

        results.append(
            {
                "file": out_path.name,
                "title": clip["title"],
                "start": clip["start"],
                "end": clip["end"],
                "duration": round(clip["end"] - clip["start"], 2),
                "reason": clip["reason"],
            }
        )

    (output_dir / "clips.json").write_text(json.dumps(results, indent=2))
    return results
