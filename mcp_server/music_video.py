"""Music video pipeline — audio-driven scene planning, generation, and stitching."""

import json
import subprocess
import shutil
import whisper
from pathlib import Path
from dataclasses import dataclass, field, asdict

# Target aspect ratio for all images and video (matches LTX 2.3 output)
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 25


@dataclass
class Scene:
    """A single scene in the music video."""
    id: int
    start: float           # seconds
    end: float             # seconds
    text: str              # lyrics for this segment
    segment_type: str      # verse, chorus, bridge, intro, outro, instrumental
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
class Storyboard:
    """Full storyboard for a music video."""
    title: str
    audio_path: str
    duration: float
    scenes: list[Scene] = field(default_factory=list)
    world_elements: dict = field(default_factory=dict)
    camera_style: str = ""

    def save(self, path: Path):
        data = {
            "title": self.title,
            "audio_path": self.audio_path,
            "duration": self.duration,
            "camera_style": self.camera_style,
            "world_elements": self.world_elements,
            "scenes": [asdict(s) for s in self.scenes],
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Storyboard":
        data = json.loads(path.read_text())
        scenes = [Scene(**s) for s in data.pop("scenes", [])]
        return cls(**data, scenes=scenes)


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
    target_duration: float = 12.0,
    max_duration: float = 30.0,
    min_duration: float = 5.0,
) -> list[Scene]:
    """Merge transcript segments into clip-sized scenes for video generation.

    Groups adjacent segments into 10-15s clips by default. Respects segment
    type boundaries (won't merge a verse into a chorus). Allows up to 30s
    for sustained moments.

    Args:
        scenes: Raw transcript segments.
        target_duration: Ideal clip length in seconds (default 12s).
        max_duration: Maximum clip length (default 30s).
        min_duration: Minimum clip length — keep merging until at least this (default 5s).
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
        same_type = (
            scene.segment_type == prev.segment_type
            or prev.segment_type == "instrumental"
            or scene.segment_type == "instrumental"
        )

        # Merge if:
        # 1. Previous scene is below minimum duration, OR
        # 2. Combined would be under target and same segment type, OR
        # 3. Previous scene is under target and combined won't exceed max
        should_merge = (
            (prev.duration < min_duration)
            or (combined_duration <= target_duration and same_type)
            or (prev.duration < target_duration and combined_duration <= max_duration and same_type)
        )

        if should_merge:
            prev.end = scene.end
            if scene.text:
                prev.text = f"{prev.text} {scene.text}".strip()
            if prev.segment_type == "instrumental" and scene.segment_type != "instrumental":
                prev.segment_type = scene.segment_type
        else:
            scene.id = len(merged)
            merged.append(Scene(
                id=scene.id,
                start=scene.start,
                end=scene.end,
                text=scene.text,
                segment_type=scene.segment_type,
            ))

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
                filter_parts.append(f"[{i}:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1[v{i}]")

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
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
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
    target_duration: float = 12.0,
    max_duration: float = 30.0,
    min_duration: float = 5.0,
) -> Storyboard:
    """Steps 1-2: Transcribe and build storyboard (no generation yet).

    Returns a Storyboard with scenes that need prompts filled in.
    Scenes are merged to target 10-15s clips (configurable), max 30s.
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
        target_duration=target_duration,
        max_duration=max_duration,
        min_duration=min_duration,
    )

    return Storyboard(
        title=title,
        audio_path=str(Path(audio_path).resolve()),
        duration=duration,
        scenes=scenes,
    )
