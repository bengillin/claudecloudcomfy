"""Music video pipeline — audio-driven scene planning, generation, and stitching."""

import json
import re
import subprocess
import shutil
import whisper
from pathlib import Path
from dataclasses import dataclass, field, asdict

# Defaults — overridable per project via storyboard
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FPS = 25


@dataclass
class WorldElement:
    """A reusable visual element in the music video world."""
    id: str                    # unique slug, e.g. "afroman", "courtroom"
    category: str              # who, what, when, where, why
    name: str                  # display name
    description: str           # detailed visual description for generation
    reference_images: list[str] = field(default_factory=list)  # paths to approved images
    source_images: list[str] = field(default_factory=list)     # user-provided input images
    seed: int = 0


@dataclass
class SceneElementRef:
    """A reference from a scene to a world element, with optional override."""
    element_id: str
    override_description: str = ""  # scene-specific variation


@dataclass
class Scene:
    """A single scene in the music video.

    A scene is a narrative tent-pole keyed to a lyric section. By default it
    produces one image and one a2v clip matching its time window. For
    higher-energy editing, attach a `shots` list to give the scene multiple
    internal cuts — each shot is independently generated and stitched in
    order. Shots come in two flavours:

      - "lipsync": a slice of the scene's audio drives an ltx23-a2v clip
        (use for alternate camera angles of a performing character).
      - "broll":   no audio conditioning, wan22-i2v produces a silent clip
        (use for cutaways — props, environment, hands, inserts).

    When `shots` is present, the scene-level image/audio/video paths are
    unused; per-shot paths live inside each shot dict.
    """
    id: int
    start: float           # seconds
    end: float             # seconds
    text: str              # lyrics for this segment
    segment_type: str      # verse, chorus, bridge, intro, outro, instrumental
    element_refs: list[dict] = field(default_factory=list)  # SceneElementRef as dicts
    prompt: str = ""       # visual prompt for image generation
    motion_prompt: str = ""  # motion/camera prompt for animation
    image_path: str = ""   # generated scene image (unused if shots present)
    audio_path: str = ""   # extracted audio segment (unused if shots present)
    video_path: str = ""   # generated video clip (unused if shots present)
    seed: int = 0
    shots: list[dict] = field(default_factory=list)  # optional internal cuts

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def frames(self) -> int:
        return max(1, int(self.duration * VIDEO_FPS))


@dataclass
class CreativeBrief:
    """Claude's creative analysis of the song — the artistic vision."""
    narrative: str = ""          # overall story arc
    mood: str = ""               # emotional tone
    visual_style: str = ""       # look and feel
    color_palette: str = ""      # dominant colors
    suggested_elements: list[dict] = field(default_factory=list)  # [{category, id, name, description}]
    suggested_scenes: list[dict] = field(default_factory=list)    # [{id, description, element_ids, visual, motion}]
    notes: str = ""              # any other creative direction


@dataclass
class Storyboard:
    """Full storyboard for a music video."""
    title: str
    audio_path: str
    duration: float
    scenes: list[Scene] = field(default_factory=list)
    elements: list[WorldElement] = field(default_factory=list)
    brief: CreativeBrief = field(default_factory=CreativeBrief)
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    camera_style: str = ""
    global_style: str = ""

    def save(self, path: Path):
        data = {
            "title": self.title,
            "audio_path": self.audio_path,
            "duration": self.duration,
            "camera_style": self.camera_style,
            "global_style": self.global_style,
            "brief": asdict(self.brief),
            "elements": [asdict(e) for e in self.elements],
            "scenes": [asdict(s) for s in self.scenes],
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Storyboard":
        data = json.loads(path.read_text())
        scenes = [Scene(**s) for s in data.pop("scenes", [])]
        elements = [WorldElement(**e) for e in data.pop("elements", [])]
        brief_data = data.pop("brief", {})
        brief = CreativeBrief(**brief_data) if brief_data else CreativeBrief()
        # Backward compat
        data.pop("world_elements", None)
        return cls(**data, scenes=scenes, elements=elements, brief=brief)

    def get_element(self, element_id: str) -> WorldElement | None:
        for e in self.elements:
            if e.id == element_id:
                return e
        return None


# ── Step 1: Transcribe ──────────────────────────────────────────────────


def transcribe(audio_path: str, model_size: str = "base") -> list[dict]:
    """Transcribe audio with Whisper. Returns segments with timestamps."""
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, word_timestamps=True)
    return result["segments"]


# ── Step 2: Build scenes from transcript ────────────────────────────────


def segments_to_scenes(segments: list[dict], audio_duration: float) -> list[Scene]:
    """Convert Whisper segments into scenes with timing."""
    scenes = []
    for i, seg in enumerate(segments):
        scenes.append(Scene(
            id=i,
            start=seg["start"],
            end=seg["end"],
            text=seg["text"].strip(),
            segment_type=_detect_segment_type(seg["text"], i, len(segments), audio_duration),
        ))

    # Fill gaps — add instrumental scenes for silence between segments
    filled = []
    prev_end = 0.0
    for scene in scenes:
        if scene.start - prev_end > 0.5:  # >0.5s gap = instrumental
            filled.append(Scene(
                id=len(filled),
                start=prev_end,
                end=scene.start,
                text="",
                segment_type="instrumental",
            ))
        scene.id = len(filled)
        filled.append(scene)
        prev_end = scene.end

    # Trailing silence
    if audio_duration - prev_end > 0.5:
        filled.append(Scene(
            id=len(filled),
            start=prev_end,
            end=audio_duration,
            text="",
            segment_type="outro",
        ))

    return filled


def _detect_segment_type(text: str, idx: int, total: int, duration: float) -> str:
    """Heuristic segment type detection."""
    t = text.lower().strip()
    if not t:
        return "instrumental"
    if any(marker in t for marker in ["[chorus]", "[hook]", "(chorus)"]):
        return "chorus"
    if any(marker in t for marker in ["[verse", "[rap", "(verse"]):
        return "verse"
    if any(marker in t for marker in ["[bridge]", "(bridge)"]):
        return "bridge"
    if any(marker in t for marker in ["[intro]", "(intro)"]):
        return "intro"
    if any(marker in t for marker in ["[outro]", "(outro)"]):
        return "outro"
    # Position heuristics
    frac = idx / max(total - 1, 1)
    if frac < 0.05:
        return "intro"
    if frac > 0.95:
        return "outro"
    return "verse"


# ── Step 3: Split audio at scene boundaries ─────────────────────────────


def split_audio(audio_path: str, scenes: list[Scene], output_dir: Path) -> list[Scene]:
    """Split audio into per-scene WAV segments."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for scene in scenes:
        out = output_dir / f"scene_{scene.id:03d}.wav"
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", str(scene.start),
            "-t", str(scene.duration),
            "-ar", "44100", "-ac", "1",
            out,
        ], capture_output=True)
        scene.audio_path = str(out)
    return scenes


# ── Step 4: Merge adjacent short scenes ─────────────────────────────────


def merge_short_scenes(
    scenes: list[Scene],
    min_duration: float = 5.0,
    max_duration: float = 30.0,
) -> list[Scene]:
    """Merge transcript segments into scenes based on musical/narrative transitions.

    Cuts happen at natural boundaries — segment type changes (verse→chorus),
    silence gaps, and mood shifts. Clip lengths vary based on what the song
    needs: a 6s hook stays short, a 20s verse that holds one vibe stays long.

    The min/max are guardrails, not targets. The song determines clip length.

    Args:
        scenes: Raw transcript segments.
        min_duration: Minimum clip length — merge below this (default 5s).
        max_duration: Maximum clip length — force a cut above this (default 30s).
    """
    if not scenes:
        return scenes

    merged = [Scene(
        id=0,
        start=scenes[0].start,
        end=scenes[0].end,
        text=scenes[0].text,
        segment_type=scenes[0].segment_type,
    )]

    for scene in scenes[1:]:
        prev = merged[-1]
        combined_duration = scene.end - prev.start

        # Detect natural transition points
        type_change = (
            scene.segment_type != prev.segment_type
            and scene.segment_type != "instrumental"
            and prev.segment_type != "instrumental"
        )
        gap = scene.start - prev.end > 0.5  # silence gap between segments
        would_exceed_max = combined_duration > max_duration
        prev_too_short = prev.duration < min_duration

        # Cut at natural transitions — UNLESS the previous clip is too short
        if prev_too_short and not would_exceed_max:
            # Keep merging — clip needs more content
            prev.end = scene.end
            if scene.text:
                prev.text = f"{prev.text} {scene.text}".strip()
            if prev.segment_type == "instrumental" and scene.segment_type != "instrumental":
                prev.segment_type = scene.segment_type
        elif type_change or gap or would_exceed_max:
            # Natural cut point — start a new scene
            merged.append(Scene(
                id=len(merged),
                start=scene.start,
                end=scene.end,
                text=scene.text,
                segment_type=scene.segment_type,
            ))
        else:
            # Same segment type, no gap, within limits — merge
            prev.end = scene.end
            if scene.text:
                prev.text = f"{prev.text} {scene.text}".strip()

    # Handle last scene being too short
    if len(merged) > 1 and merged[-1].duration < min_duration:
        merged[-2].end = merged[-1].end
        if merged[-1].text:
            merged[-2].text = f"{merged[-2].text} {merged[-1].text}".strip()
        merged.pop()

    # Re-number
    for i, s in enumerate(merged):
        s.id = i

    return merged


# ── Step 2-alt: Build scenes from user-provided lyrics ──────────────────


_SECTION_TYPE_KEYWORDS = [
    ("drop", ["drop", "beat drop"]),
    ("chorus", ["chorus", "hook", "refrain"]),
    ("pre-chorus", ["pre-chorus", "pre chorus", "prechorus", "build", "rise"]),
    ("verse", ["verse", "rap"]),
    ("bridge", ["bridge"]),
    ("intro", ["intro", "opening"]),
    ("outro", ["outro", "ending", "coda"]),
    ("breakdown", ["breakdown", "break"]),
    ("interlude", ["interlude"]),
]


def _segment_type_from_section_name(name: str) -> str:
    n = (name or "").lower()
    for seg_type, keywords in _SECTION_TYPE_KEYWORDS:
        if any(k in n for k in keywords):
            return seg_type
    return "verse"


def _parse_lyrics_sections(lyrics: str) -> list[tuple[str, list[str]]]:
    """Parse lyrics into [(section_name, [content_lines]), ...].

    Recognizes `[Verse 1]`, `[Chorus]`, `(Hook)`, `# Verse`, etc. as section
    markers. Everything before the first marker becomes an implicit 'intro'.
    If no markers are found at all, the whole block becomes a single 'verse'.
    """
    section_pattern = re.compile(r"^\s*[\[\(\#]\s*([^\]\)\#]+?)\s*[\]\)\#]?\s*$")
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    for raw in lyrics.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = section_pattern.match(line)
        # Match only tight marker lines — not stray brackets mid-verse.
        is_marker = bool(m) and len(line) <= 60 and any(c in line for c in "[](){}#")
        if is_marker:
            if current_name is not None or current_lines:
                sections.append((current_name or "intro", current_lines))
            current_name = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None or current_lines:
        sections.append((current_name or "verse", current_lines))
    return sections


def scenes_from_lyrics(
    lyrics: str,
    duration: float,
    audio_path: str,
    min_duration: float = 5.0,
    max_duration: float = 30.0,
    target_duration: float = 12.0,
) -> tuple[list[Scene], dict]:
    """Build scenes from user-provided lyrics, beat-snapped to the song.

    Strategy:
      1. Parse [Section] markers into (name, lines) groups.
      2. Weight each section's time slice by its line count (longer sections =
         more time). Unless weight-of-one-line-in-a-3-min-song < min_duration,
         in which case floor at min_duration.
      3. Hand the resulting scenes through split_long_scenes_on_beats so any
         section longer than max_duration gets subdivided on musical bars.

    Returns (scenes, info). info carries tempo, section count, final scene count.
    """
    sections = _parse_lyrics_sections(lyrics)
    if not sections:
        sections = [("verse", [lyrics.strip()] if lyrics.strip() else [])]

    weights = [max(1, len(lines)) for _, lines in sections]
    total_weight = sum(weights)
    portions = [(w / total_weight) * duration for w in weights]

    # Floor each section at min_duration by stealing from the longest neighbor.
    # Avoid producing a 2s scene just because one line had 4 words.
    for i, p in enumerate(portions):
        if p < min_duration and duration >= min_duration * len(sections):
            deficit = min_duration - p
            donor = max(range(len(portions)), key=lambda j: portions[j] if j != i else -1)
            portions[donor] -= deficit
            portions[i] = min_duration

    scenes: list[Scene] = []
    cursor = 0.0
    for idx, ((name, lines), portion) in enumerate(zip(sections, portions)):
        start = cursor
        end = cursor + portion if idx < len(sections) - 1 else duration
        scenes.append(Scene(
            id=idx,
            start=round(start, 3),
            end=round(end, 3),
            text=" ".join(lines),
            segment_type=_segment_type_from_section_name(name),
        ))
        cursor = end

    scenes, beat_info = split_long_scenes_on_beats(
        scenes, audio_path, max_duration=max_duration, target_duration=target_duration,
    )

    info = {
        "source": "user_lyrics",
        "section_count": len(sections),
        "scene_count": len(scenes),
    }
    if beat_info:
        info.update(beat_info)
    return scenes, info


# ── Step 4b: Beat-align long scenes ─────────────────────────────────────


def split_long_scenes_on_beats(
    scenes: list[Scene],
    audio_path: str,
    max_duration: float,
    target_duration: float = 12.0,
) -> tuple[list[Scene], dict]:
    """Split any scene longer than max_duration on musical bar boundaries.

    When Whisper produces a giant scene (e.g. a 120s instrumental with no
    detected vocals, or sample-heavy music), fall back to librosa beat
    tracking and cut at phrase boundaries near target_duration. Preserves
    each scene's text and segment_type on the first subclip.

    Returns (new_scenes, info). info is empty if nothing needed splitting.
    """
    needs_split = any(s.duration > max_duration for s in scenes)
    if not needs_split:
        return scenes, {}

    import librosa
    import numpy as np

    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    tempo_bpm = float(tempo) if np.isscalar(tempo) else float(tempo[0])
    sec_per_beat = 60.0 / tempo_bpm if tempo_bpm > 0 else 0.5
    bar_beats = 4  # assume 4/4
    # Beats per phrase — nearest multiple of bar_beats whose duration ≈ target
    beats_per_phrase = max(bar_beats, round(target_duration / sec_per_beat / bar_beats) * bar_beats)

    new_scenes: list[Scene] = []
    split_count = 0
    for s in scenes:
        if s.duration <= max_duration:
            new_scenes.append(s)
            continue

        split_count += 1
        scene_beats = beat_times[(beat_times >= s.start) & (beat_times <= s.end)]

        if len(scene_beats) < beats_per_phrase:
            # No usable beat grid — fall back to even splits at target_duration
            n_splits = max(2, int(round(s.duration / target_duration)))
            step = s.duration / n_splits
            cuts = [s.start + k * step for k in range(n_splits)] + [s.end]
        else:
            cuts = [s.start]
            for idx in range(beats_per_phrase, len(scene_beats), beats_per_phrase):
                candidate = float(scene_beats[idx])
                # Only accept if it yields reasonable subclip lengths
                if candidate - cuts[-1] >= max(3.0, target_duration * 0.4) and s.end - candidate >= 3.0:
                    cuts.append(candidate)
            cuts.append(s.end)

        for k in range(len(cuts) - 1):
            new_scenes.append(Scene(
                id=-1,
                start=cuts[k],
                end=cuts[k + 1],
                text=s.text if k == 0 else "",
                segment_type=s.segment_type,
                element_refs=list(s.element_refs) if k == 0 else [],
                prompt=s.prompt if k == 0 else "",
                motion_prompt=s.motion_prompt if k == 0 else "",
                seed=s.seed,
            ))

    for i, sc in enumerate(new_scenes):
        sc.id = i

    return new_scenes, {
        "tempo_bpm": round(tempo_bpm, 1),
        "beats_per_phrase": beats_per_phrase,
        "scenes_split": split_count,
        "scene_count_before": len(scenes),
        "scene_count_after": len(new_scenes),
    }


# ── Step 5: Stitch clips ────────────────────────────────────────────────


def stitch_video(
    scenes: list[Scene],
    audio_path: str,
    output_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    crossfade: float = 0.0,
) -> Path:
    """Concatenate video clips and overlay original audio track.

    Each clip gets trimmed to its exact scene duration, then concatenated.
    Original audio replaces any model-generated audio.
    """
    # Build concat file
    concat_file = output_path.parent / "concat.txt"
    lines = []
    for scene in scenes:
        if not scene.video_path or not Path(scene.video_path).exists():
            continue
        # Escape single quotes in path
        escaped = scene.video_path.replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    concat_file.write_text("\n".join(lines))

    if crossfade > 0 and len(lines) > 1:
        # Use xfade filter for crossfades between clips
        # Build a complex filter chain
        inputs = []
        for scene in scenes:
            if scene.video_path and Path(scene.video_path).exists():
                inputs.extend(["-i", scene.video_path])

        n = len(inputs) // 2
        if n > 1:
            filter_parts = []
            # Scale all inputs to target resolution
            for i in range(n):
                filter_parts.append(f"[{i}:v]scale={width}:{height},setsar=1[v{i}]")

            # Chain xfade filters
            prev = "[v0]"
            for i in range(1, n):
                offset = sum(s.duration for s in scenes[:i]) - crossfade * i
                out = f"[xf{i}]" if i < n - 1 else "[vout]"
                filter_parts.append(
                    f"{prev}[v{i}]xfade=transition=fade:duration={crossfade}:offset={offset:.3f}{out}"
                )
                prev = out

            filter_str = ";\n".join(filter_parts)
            subprocess.run([
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_str,
                "-map", "[vout]",
                "-i", audio_path,
                "-map", f"{n}:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "320k",
                "-shortest",
                str(output_path),
            ], capture_output=True)
            concat_file.unlink(missing_ok=True)
            return output_path

    # Simple concat (no crossfade)
    # First concat video clips
    temp_video = output_path.parent / "temp_concat.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-an",
        str(temp_video),
    ], capture_output=True)

    # Replace audio with original track
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(temp_video),
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "320k",
        "-map", "0:v", "-map", "1:a",
        "-shortest",
        str(output_path),
    ], capture_output=True)

    temp_video.unlink(missing_ok=True)
    concat_file.unlink(missing_ok=True)
    return output_path


# ── Full pipeline ───────────────────────────────────────────────────────


def plan(
    audio_path: str,
    title: str,
    min_duration: float = 5.0,
    max_duration: float = 30.0,
    lyrics: str | None = None,
) -> tuple[Storyboard, dict]:
    """Steps 1-2: Build a storyboard from audio (no generation yet).

    If `lyrics` is provided and non-empty, skip Whisper entirely: parse
    [Section] markers into scenes, distribute time proportionally to line
    count, then beat-snap long sections via librosa. This is the right
    path for instrumental / sample-heavy / pitched-vocal songs where
    Whisper can't detect structure.

    Otherwise, transcribe with Whisper and cut at natural transitions,
    falling back to beat-based splits when any scene exceeds max_duration.

    Returns (Storyboard, info). info contains beat-analysis results and
    warnings.
    """
    # Get audio duration
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    duration = float(result.stdout.strip())

    warnings: list[dict] = []
    beat_info: dict = {}

    if lyrics and lyrics.strip():
        # User-provided lyrics — skip Whisper, parse sections, beat-snap.
        try:
            scenes, beat_info = scenes_from_lyrics(
                lyrics, duration, audio_path,
                min_duration=min_duration,
                max_duration=max_duration,
            )
        except Exception as e:
            warnings.append({
                "type": "lyrics_parse_failed",
                "message": f"Could not parse provided lyrics ({e}) — falling back to Whisper.",
            })
            lyrics = None  # trigger the Whisper branch below

    if not (lyrics and lyrics.strip()):
        # Whisper branch
        segments = transcribe(audio_path)
        scenes = segments_to_scenes(segments, duration)
        scenes = merge_short_scenes(
            scenes,
            min_duration=min_duration,
            max_duration=max_duration,
        )

        # Vocal-coverage sanity check — if Whisper found very little speech,
        # the scene structure is going to be a single giant instrumental blob.
        vocal_seconds = sum(s.duration for s in scenes if s.text)
        vocal_ratio = vocal_seconds / duration if duration > 0 else 0.0
        if vocal_ratio < 0.2:
            warnings.append({
                "type": "sparse_vocals",
                "message": (
                    f"Only {vocal_ratio*100:.0f}% of the track was transcribed as vocals — "
                    "likely sample-heavy or mostly instrumental. Scene cuts may not follow "
                    "the song's real structure. Paste the lyrics into the 'Lyrics (optional)' "
                    "field and re-plan to structure scenes by song sections instead."
                ),
                "vocal_seconds": round(vocal_seconds, 1),
                "total_seconds": round(duration, 1),
            })

        # Beat-align any scenes that still exceed max_duration after merging.
        try:
            scenes, beat_info = split_long_scenes_on_beats(
                scenes, audio_path, max_duration=max_duration,
            )
        except Exception as e:  # librosa missing or audio load failure
            warnings.append({
                "type": "beat_analysis_failed",
                "message": f"Could not run beat analysis for long-scene splitting: {e}",
            })

    # Final guardrail: warn if anything is still over max_duration.
    over_max = [
        {"id": s.id, "duration": round(s.duration, 1)}
        for s in scenes if s.duration > max_duration
    ]
    if over_max:
        warnings.append({
            "type": "scene_exceeds_max_duration",
            "message": (
                f"{len(over_max)} scene(s) still exceed max_duration={max_duration}s — "
                "a2v clips will hit model limits. Manually split these scenes."
            ),
            "scenes": over_max,
        })

    # Auto-suggest 5W elements from lyrics
    all_text = " ".join(s.text for s in scenes if s.text).lower()
    suggested = _suggest_elements(all_text, title)

    # Auto-assign elements to scenes and suggest prompts
    _assign_elements_to_scenes(scenes, suggested)

    sb = Storyboard(
        title=title,
        audio_path=str(Path(audio_path).resolve()),
        duration=duration,
        scenes=scenes,
        elements=suggested,
    )
    info = {"beat_info": beat_info, "warnings": warnings}
    return sb, info


def _assign_elements_to_scenes(scenes: list[Scene], elements: list[WorldElement]):
    """Match elements to scenes based on keyword overlap, then build suggested prompts."""
    # Build keyword lookup per element (reuse the pattern dictionaries)
    element_keywords = {}
    all_patterns = {
        **{eid: kws for eid, kws in [
            ("narrator", ["i ", "i'm ", "my ", "me "]),
            ("police", ["police", "cops", "officer", "sheriff", "deputy", "raid"]),
            ("crowd", ["crowd", "people", "audience", "fans"]),
            ("lover", ["baby", "girl", "babe"]),
            ("antagonist", ["they ", "them "]),
            ("street", ["street", "road", "block", "neighborhood"]),
            ("house", ["house", "home", "door", "room", "crib", "front door", "gate"]),
            ("court", ["court", "judge", "trial", "jury", "gavel", "ruling"]),
            ("stage", ["stage", "mic", "concert", "perform"]),
            ("club", ["club", "bar", "party"]),
            ("car", ["car", "ride", "driving"]),
            ("jail", ["jail", "prison", "cell"]),
            ("night", ["night", "dark", "midnight"]),
            ("day", ["sun", "morning", "daylight"]),
            ("golden-hour", ["sunset", "sunrise", "golden"]),
            ("money", ["money", "cash", "dollar", "bucks"]),
            ("gun", ["gun", "rifle", "weapon"]),
            ("phone", ["phone", "call"]),
            ("music", ["guitar", "mic", "beat", "song", "music", "rap"]),
            ("camera", ["camera", "film", "footage", "surveillance"]),
            ("drugs", ["smoke", "weed", "blunt"]),
            ("defiance", ["fight", "stand", "defy", "won't"]),
            ("triumph", ["win", "victory", "ruling", "case closed"]),
            ("anger", ["angry", "mad", "rage"]),
            ("joy", ["happy", "love", "celebrate"]),
            ("pain", ["hurt", "cry", "tears"]),
        ]},
    }

    element_ids = {e.id for e in elements}

    for scene in scenes:
        text = scene.text.lower()
        matched = []
        for eid, keywords in all_patterns.items():
            if eid in element_ids and any(kw in text for kw in keywords):
                matched.append(eid)

        # Always include narrator for scenes with first-person lyrics
        if "narrator" in element_ids and any(kw in text for kw in ["i ", "i'm ", "my "]):
            if "narrator" not in matched:
                matched.append("narrator")

        scene.element_refs = [{"element_id": eid, "override_description": ""} for eid in matched]

        # Build suggested prompt from matched elements
        parts = []
        by_cat = {}
        for eid in matched:
            el = next((e for e in elements if e.id == eid), None)
            if el:
                by_cat.setdefault(el.category, []).append(el.name)

        if "who" in by_cat:
            parts.append(", ".join(by_cat["who"]))
        if "where" in by_cat:
            parts.append("in " + " and ".join(by_cat["where"]))
        if "when" in by_cat:
            parts.append(", ".join(by_cat["when"]) + " atmosphere")
        if "what" in by_cat:
            parts.append("with " + ", ".join(by_cat["what"]))
        if "why" in by_cat:
            parts.append(", ".join(by_cat["why"]) + " energy")

        if parts:
            scene.prompt = ", ".join(parts) + ", cinematic, photorealistic"

        # Motion prompt from segment type
        motion_hints = {
            "intro": "Slow establishing shot, camera gently pushes in",
            "verse": "Medium shot, subtle camera movement, character performs to camera",
            "chorus": "Dynamic camera, energy builds, movement syncs to beat",
            "bridge": "Shifting perspective, transitional mood, camera drifts",
            "outro": "Camera slowly pulls back, fading energy",
            "instrumental": "Abstract motion, flowing visuals, atmospheric",
        }
        scene.motion_prompt = motion_hints.get(scene.segment_type, "Cinematic camera movement")


def _suggest_elements(text: str, title: str) -> list:
    """Extract suggested 5W elements from lyrics via keyword matching.

    This is a rough first pass — Claude refines these via comfy_mv_set_brief.
    """
    elements = []

    # WHO — look for character references
    who_patterns = {
        "narrator": (["i ", "i'm ", "my ", "me ", "i've "],
                     "The main character and narrator, medium close-up, face clearly visible, expressive"),
        "police": (["police", "cops", "officer", "sheriff", "deputy", "swat"],
                   "Law enforcement officers in uniform, tactical gear, badges visible, authoritative stance"),
        "crowd": (["crowd", "people", "audience", "fans", "everybody"],
                  "Group of onlookers or fans, diverse, reactive expressions, background crowd"),
        "lover": (["baby", "girl", "babe", "honey", "darling"],
                  "Romantic interest, attractive, warm expression, intimate framing"),
        "antagonist": (["they ", "them ", "enemy", "hater"],
                       "Opposing figure, threatening or dismissive demeanor, dramatic lighting"),
    }
    for char_id, (keywords, desc) in who_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=char_id, category="who",
                name=char_id.title(),
                description=desc,
            ))

    # WHERE — locations
    where_patterns = {
        "street": (["street", "road", "sidewalk", "block", "hood", "neighborhood"],
                   "Urban street scene, sidewalks, buildings, streetlights, wide establishing shot"),
        "house": (["house", "home", "door", "room", "kitchen", "bedroom", "crib"],
                  "Residential house exterior and interior, front door, porch, lived-in rooms"),
        "court": (["court", "judge", "trial", "jury", "gavel"],
                  "Courtroom interior, judge's bench, witness stand, jury box, wood paneling, American flag"),
        "stage": (["stage", "mic", "concert", "perform", "show"],
                  "Performance stage with microphone, spotlights, smoke, crowd visible in background"),
        "club": (["club", "bar", "party", "dance floor"],
                 "Nightclub interior, neon lights, dance floor, bar, dim moody lighting"),
        "car": (["car", "ride", "driving", "whip", "trunk"],
                "Vehicle interior or exterior, dashboard, steering wheel, road visible"),
        "jail": (["jail", "prison", "cell", "locked up", "behind bars"],
                 "Jail cell or prison corridor, bars, concrete walls, harsh fluorescent lighting"),
    }
    for loc_id, (keywords, desc) in where_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=loc_id, category="where",
                name=loc_id.title(),
                description=desc,
            ))

    # WHEN — time/atmosphere
    when_patterns = {
        "night": (["night", "dark", "midnight", "moonlight", "after dark"],
                  "Nighttime atmosphere, dark sky, artificial lighting, shadows, moody"),
        "day": (["sun", "morning", "daylight", "bright", "noon"],
                "Daytime, natural sunlight, clear sky, bright and open"),
        "golden-hour": (["sunset", "sunrise", "golden", "dusk", "dawn"],
                        "Golden hour light, warm orange tones, long shadows, cinematic warmth"),
    }
    for time_id, (keywords, desc) in when_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=time_id, category="when",
                name=time_id.replace("-", " ").title(),
                description=desc,
            ))

    # WHAT — key objects/actions
    what_patterns = {
        "money": (["money", "cash", "dollar", "bucks", "bread", "paid"],
                  "Cash money, bills spread out, close-up detail, dramatic lighting"),
        "gun": (["gun", "rifle", "weapon", "shoot", "trigger"],
                "Firearms, tactical weapons, dramatic angle, tension"),
        "phone": (["phone", "call", "text", "screen"],
                  "Smartphone screen, notifications, close-up on display"),
        "music": (["guitar", "mic", "beat", "song", "music", "rap", "sing"],
                  "Musical instruments, microphone, recording equipment, performance energy"),
        "camera": (["camera", "film", "record", "footage", "surveillance"],
                   "Surveillance cameras, security footage aesthetic, lens detail"),
        "drugs": (["smoke", "weed", "high", "blunt", "joint"],
                  "Smoke drifting through air, hazy atmosphere, close-up detail"),
    }
    for obj_id, (keywords, desc) in what_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=obj_id, category="what",
                name=obj_id.title(),
                description=desc,
            ))

    # WHY — mood/emotion
    why_patterns = {
        "defiance": (["fight", "stand", "defy", "resist", "never", "won't"],
                     "Defiant energy, standing ground, unwavering confidence, bold composition"),
        "triumph": (["win", "victory", "champion", "ruling", "case closed"],
                    "Triumphant celebration, arms raised, victorious, golden light, uplifting"),
        "anger": (["angry", "mad", "rage", "furious", "hate"],
                  "Intense anger, clenched fists, harsh lighting, red tones, aggressive energy"),
        "joy": (["happy", "love", "celebrate", "party", "good"],
                "Pure joy, smiles, warm light, vibrant colors, celebratory"),
        "pain": (["hurt", "cry", "tears", "pain", "broken"],
                 "Emotional pain, vulnerability, muted colors, soft focus, introspective"),
    }
    for mood_id, (keywords, desc) in why_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=mood_id, category="why",
                name=mood_id.title(),
                description=desc,
            ))

    return elements
