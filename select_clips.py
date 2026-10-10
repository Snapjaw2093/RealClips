"""Step 2: the "editor".

Reads the transcript and asks Claude to pick the best moments for Shorts.
Claude replies with JSON; we then CHECK that reply carefully, because an AI
can occasionally return clips that are too long, overlap, or fall outside
the video. If the check fails we ask once more, telling Claude what was wrong.

Run by itself (uses the saved transcript from step 1):
    python select_clips.py output/transcript.json
"""

import json
import os
import sys

# The Claude model that picks the clips. Easy to swap, e.g. "claude-opus-5-5".
CLAUDE_MODEL = "claude-sonnet-5-5"

# How many seconds of wiggle room we allow around the min/max clip length.
# (Claude counts seconds loosely, and we snap its times to word boundaries.)
LENGTH_TOLERANCE = 3.0


class ClipSelectionError(Exception):
    """Raised when we can't get a usable answer from Claude."""


def get_client():
    """Create the Claude client, with a friendly error if the key is missing."""
    # python-dotenv reads the .env file and puts its values into the environment.
    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ClipSelectionError(
            "ANTHROPIC_API_KEY is not set.\n"
            "  1. Copy .env.example to .env\n"
            "  2. Paste your key after ANTHROPIC_API_KEY=\n"
            "  (Get a key at https://console.anthropic.com/)"
        )
    import anthropic

    return anthropic.Anthropic()


def format_transcript(words, max_words_per_line=18):
    """Turn the word list into readable lines, each starting with its time.

    Example line:  [12.40] So the kitchen was completely remodeled last year.

    Claude reads these times and uses them to say where a clip starts and ends.
    We break lines at sentence endings (. ? !) or after ~18 words.
    """
    lines = []
    current = []
    for w in words:
        current.append(w)
        ends_sentence = w["word"].endswith((".", "?", "!"))
        if ends_sentence or len(current) >= max_words_per_line:
            lines.append(f"[{current[0]['start']:.2f}] " + " ".join(x["word"] for x in current))
            current = []
    if current:
        lines.append(f"[{current[0]['start']:.2f}] " + " ".join(x["word"] for x in current))
    return "\n".join(lines)


def build_prompt(transcript_text, duration, num_clips, min_len, max_len):
    """The instructions we send to Claude."""
    return f"""You are a short-form video editor for real estate agents. Below is the timestamped transcript of a {duration:.0f}-second video (a listing walkthrough or market update). Each line starts with its start time in seconds.

Pick the {num_clips} best moments to turn into vertical YouTube Shorts.

Rules for every clip:
- Length: between {min_len} and {max_len} seconds (end minus start).
- It must OPEN with a strong hook: a surprising fact, a bold claim, a question, or a vivid detail that makes a viewer stop scrolling.
- It must END at a natural stopping point, such as the end of a sentence or a complete thought. Never cut mid-sentence.
- It must make sense on its own for someone who has not seen the rest of the video.
- Clips must not overlap each other.
- Start and end times are in seconds and must fall inside the video (0 to {duration:.0f}).

Reply with ONLY a JSON object, no other text, in exactly this shape:
{{"clips": [{{"start": 12.4, "end": 58.9, "title": "Short catchy title", "reason": "One sentence on why this moment works."}}]}}

Transcript:
{transcript_text}"""


def extract_json(text):
    """Pull the JSON object out of Claude's reply.

    Claude is asked for JSON only, but sometimes wraps it in ```json fences,
    so we grab everything from the first { to the last }.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in the reply")
    return json.loads(text[start : end + 1])


def snap_to_words(clip, words):
    """Move Claude's start/end times onto real word boundaries.

    The start moves to the beginning of the nearest word, the end to the end
    of the nearest word. That way a clip never begins or ends in the middle
    of a word.
    """
    start = min(words, key=lambda w: abs(w["start"] - clip["start"]))["start"]
    end = min(words, key=lambda w: abs(w["end"] - clip["end"]))["end"]
    return start, end


def validate_clips(raw, words, duration, num_clips, min_len, max_len):
    """Check Claude's answer. Returns clean clips, or raises ValueError."""
    clips = raw.get("clips") if isinstance(raw, dict) else None
    if not isinstance(clips, list) or len(clips) != num_clips:
        raise ValueError(f'expected a "clips" list with exactly {num_clips} items')

    cleaned = []
    for i, c in enumerate(clips, start=1):
        try:
            start, end = float(c["start"]), float(c["end"])
            title, reason = str(c["title"]).strip(), str(c["reason"]).strip()
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"clip {i} needs numeric start/end plus title and reason")
        if not (0 <= start < end <= duration + 1):
            raise ValueError(f"clip {i}: times {start}-{end} are outside the {duration:.0f}s video")

        start, end = snap_to_words({"start": start, "end": end}, words)
        length = end - start
        if not (min_len - LENGTH_TOLERANCE <= length <= max_len + LENGTH_TOLERANCE):
            raise ValueError(
                f"clip {i} is {length:.0f}s long; it must be {min_len}-{max_len}s"
            )
        cleaned.append(
            {"start": round(start, 2), "end": round(end, 2), "title": title, "reason": reason}
        )

    # Sort by time, then make sure no clip starts before the previous one ends.
    cleaned.sort(key=lambda c: c["start"])
    for prev, nxt in zip(cleaned, cleaned[1:]):
        if nxt["start"] < prev["end"]:
            raise ValueError("clips overlap each other")
    return cleaned


def load_picks(path, transcript, min_len, max_len):
    """Load hand-written clip picks from a JSON file instead of asking Claude.

    Handy when you have no API key, or want to override the AI's choices.
    The file looks like the AI's answer: {"clips": [{"start", "end", "title",
    "reason"}, ...]} (a bare list works too, and "reason" is optional). The
    picks go through the SAME checks as Claude's answer: snapped to word
    boundaries, length-checked, and no overlaps.
    """
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise ClipSelectionError(f"Could not read picks file {path}: {e}")
    clips = raw.get("clips") if isinstance(raw, dict) else raw
    if not isinstance(clips, list) or not clips:
        raise ClipSelectionError(f'{path} must contain a non-empty "clips" list')
    clips = [{"reason": "", **c} if isinstance(c, dict) else c for c in clips]
    try:
        return validate_clips(
            {"clips": clips}, transcript["words"], transcript["duration"], len(clips), min_len, max_len
        )
    except ValueError as e:
        raise ClipSelectionError(f"Problem in {path}: {e}")


def select_clips(transcript, num_clips, min_len, max_len, client=None):
    """Ask Claude for the best clips. Returns a list of clip dicts.

    `client` can be passed in for testing (a fake Claude). Normally we make
    a real one.
    """
    words, duration = transcript["words"], transcript["duration"]
    if not words:
        raise ClipSelectionError("The transcript is empty - no speech was found in the video.")
    client = client or get_client()

    prompt = build_prompt(format_transcript(words), duration, num_clips, min_len, max_len)
    messages = [{"role": "user", "content": prompt}]

    last_problem = None
    for attempt in (1, 2):  # one try, plus one retry if the answer is unusable
        print(f"Asking Claude to pick {num_clips} clips (attempt {attempt})...")
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            messages=messages,
        )
        if response.stop_reason == "refusal":
            raise ClipSelectionError("Claude declined this request.")
        reply = "".join(b.text for b in response.content if b.type == "text")

        try:
            return validate_clips(extract_json(reply), words, duration, num_clips, min_len, max_len)
        except ValueError as problem:  # json.JSONDecodeError is a ValueError too
            last_problem = problem
            print(f"  Problem with Claude's answer: {problem}")
            # Show Claude its own answer plus what was wrong, then try again.
            messages = messages + [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That answer had a problem: {problem}. "
                    "Please reply again with ONLY the corrected JSON object.",
                },
            ]

    raise ClipSelectionError(f"Claude did not return usable clips after 2 tries: {last_problem}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python select_clips.py output/transcript.json")
    # Imported here to reuse the settings from clipper.py (one place to edit).
    from clipper import MAX_CLIP_SECONDS, MIN_CLIP_SECONDS, NUM_CLIPS

    with open(sys.argv[1]) as f:
        data = json.load(f)
    try:
        picks = select_clips(data, NUM_CLIPS, MIN_CLIP_SECONDS, MAX_CLIP_SECONDS)
    except ClipSelectionError as e:
        sys.exit(f"Error: {e}")
    print(json.dumps(picks, indent=2))
