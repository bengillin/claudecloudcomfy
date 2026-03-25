"""Music video pipeline — audio-driven scene planning, generation, and stitching."""

import json
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
    """A single scene in the music video."""
    id: int
    start: float           # seconds
    end: float             # seconds
    text: str              # lyrics for this segment
    segment_type: str      # verse, chorus, bridge, intro, outro, instrumental
    element_refs: list[dict] = field(default_factory=list)  # SceneElementRef as dicts
    prompt: str = ""       # visual prompt for image generation
    motion_prompt: str = ""  # motion/camera prompt for animation
    image_path: str = ""   # generated scene image
    audio_path: str = ""   # extracted audio segment
    video_path: str = ""   # generated video clip
    seed: int = 0

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
) -> Storyboard:
    """Steps 1-2: Transcribe and build storyboard (no generation yet).

    Returns a Storyboard with scenes that need prompts filled in.
    Scenes are cut at natural transitions (segment type changes, gaps).
    Min/max are guardrails — the song determines clip length.
    """
    # Get audio duration
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    duration = float(result.stdout.strip())

    # Transcribe
    segments = transcribe(audio_path)

    # Build scenes
    scenes = segments_to_scenes(segments, duration)
    scenes = merge_short_scenes(
        scenes,
        min_duration=min_duration,
        max_duration=max_duration,
    )

    # Auto-suggest 5W elements from lyrics
    all_text = " ".join(s.text for s in scenes if s.text).lower()
    suggested = _suggest_elements(all_text, title)

    return Storyboard(
        title=title,
        audio_path=str(Path(audio_path).resolve()),
        duration=duration,
        scenes=scenes,
        elements=suggested,
    )


def _suggest_elements(text: str, title: str) -> list:
    """Extract suggested 5W elements from lyrics via keyword matching.

    This is a rough first pass — Claude refines these via comfy_mv_set_brief.
    """
    elements = []

    # WHO — look for character references
    who_patterns = {
        "narrator": ["i ", "i'm ", "my ", "me ", "i've "],
        "police": ["police", "cops", "officer", "sheriff", "deputy", "swat"],
        "crowd": ["crowd", "people", "audience", "fans", "everybody"],
        "lover": ["baby", "girl", "babe", "honey", "darling"],
        "antagonist": ["they ", "them ", "enemy", "hater"],
    }
    for char_id, keywords in who_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=char_id, category="who",
                name=char_id.title(),
                description=f"Character suggested from lyrics — needs visual description",
            ))

    # WHERE — locations
    where_patterns = {
        "street": ["street", "road", "sidewalk", "block", "hood", "neighborhood"],
        "house": ["house", "home", "door", "room", "kitchen", "bedroom", "crib"],
        "court": ["court", "judge", "trial", "jury", "gavel"],
        "stage": ["stage", "mic", "concert", "perform", "show"],
        "club": ["club", "bar", "party", "dance floor"],
        "car": ["car", "ride", "driving", "whip", "trunk"],
        "jail": ["jail", "prison", "cell", "locked up", "behind bars"],
    }
    for loc_id, keywords in where_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=loc_id, category="where",
                name=loc_id.title(),
                description=f"Location suggested from lyrics — needs visual description",
            ))

    # WHEN — time/atmosphere
    when_patterns = {
        "night": ["night", "dark", "midnight", "moonlight", "after dark"],
        "day": ["sun", "morning", "daylight", "bright", "noon"],
        "golden-hour": ["sunset", "sunrise", "golden", "dusk", "dawn"],
    }
    for time_id, keywords in when_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=time_id, category="when",
                name=time_id.replace("-", " ").title(),
                description=f"Time/atmosphere suggested from lyrics — needs visual description",
            ))

    # WHAT — key objects/actions
    what_patterns = {
        "money": ["money", "cash", "dollar", "bucks", "bread", "paid"],
        "gun": ["gun", "rifle", "weapon", "shoot", "trigger"],
        "phone": ["phone", "call", "text", "screen"],
        "music": ["guitar", "mic", "beat", "song", "music", "rap", "sing"],
        "camera": ["camera", "film", "record", "footage", "surveillance"],
        "drugs": ["smoke", "weed", "high", "blunt", "joint"],
    }
    for obj_id, keywords in what_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=obj_id, category="what",
                name=obj_id.title(),
                description=f"Object/action suggested from lyrics — needs visual description",
            ))

    # WHY — mood/emotion (always suggest at least one)
    why_patterns = {
        "defiance": ["fight", "stand", "defy", "resist", "never", "won't"],
        "triumph": ["win", "victory", "champion", "ruling", "case closed"],
        "anger": ["angry", "mad", "rage", "furious", "hate"],
        "joy": ["happy", "love", "celebrate", "party", "good"],
        "pain": ["hurt", "cry", "tears", "pain", "broken"],
    }
    for mood_id, keywords in why_patterns.items():
        if any(kw in text for kw in keywords):
            elements.append(WorldElement(
                id=mood_id, category="why",
                name=mood_id.title(),
                description=f"Mood/emotion suggested from lyrics — needs visual description",
            ))

    return elements
