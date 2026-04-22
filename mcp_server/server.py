"""Comfy Cloud MCP Server — gives Claude hands for creative generation."""

import functools
import json
import os
import re
import subprocess
import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import COMFY_SH, PRESETS_DIR, DOWNLOADS_DIR, PROJECTS_DIR

mcp = FastMCP(
    "comfy-cloud",
    instructions=(
        "Creative generation tools powered by Comfy Cloud. "
        "Use comfy_list_presets to discover available generation presets, "
        "then read the comfy://presets/{name} resource for prompt guidance "
        "before generating. After generation, view the output image/video "
        "to evaluate quality and iterate if needed."
    ),
)


# ── Helpers ──────────────────────────────────────────────────────────────


class ComfyError(Exception):
    """Structured error from comfy.sh with category for agent recovery."""

    def __init__(self, message: str, category: str = "unknown", hint: str = ""):
        super().__init__(message)
        self.category = category
        self.hint = hint


def _classify_error(stderr: str, stdout: str) -> tuple[str, str]:
    """Classify a comfy.sh error into (category, hint) for agent recovery."""
    text = (stderr + stdout).lower()
    if "401" in text or "unauthorized" in text or "api key" in text:
        return "auth", "Check COMFY_API_KEY in .env"
    if "429" in text or "rate limit" in text:
        return "rate_limit", "Wait a moment and retry"
    if "402" in text or "quota" in text or "insufficient" in text:
        return "quota", "Check Comfy Cloud subscription/credits"
    if "404" in text or "not found" in text:
        return "not_found", "Check the resource/endpoint exists"
    if "timeout" in text:
        return "timeout", "Job may still be running — check with comfy_job_status"
    if "upload" in text and ("too large" in text or "size" in text):
        return "file_too_large", "Reduce file size before uploading"
    return "unknown", ""


def _run_comfy(*args: str, timeout: int = 300) -> str:
    """Run a comfy.sh command and return stdout."""
    try:
        result = subprocess.run(
            [str(COMFY_SH), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(COMFY_SH.parent),
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        raise ComfyError(
            f"comfy.sh {' '.join(args)} timed out after {timeout}s",
            category="timeout",
            hint="Job may still be running — check with comfy_job_status",
        )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        category, hint = _classify_error(result.stderr, result.stdout)
        raise ComfyError(
            f"comfy.sh {' '.join(args)} failed: {error}",
            category=category,
            hint=hint,
        )
    return result.stdout.strip()


def _error_json(e: ComfyError) -> str:
    """Format a ComfyError as structured JSON for the agent."""
    result = {"status": "error", "error": str(e), "category": e.category}
    if e.hint:
        result["hint"] = e.hint
    return json.dumps(result)


def _handle_errors(fn):
    """Decorator: catch ComfyError and return structured JSON instead of raising."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ComfyError as e:
            return _error_json(e)

    return wrapper


def _load_preset(name: str) -> dict:
    """Load a preset JSON config by name."""
    path = PRESETS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Preset '{name}' not found")
    return json.loads(path.read_text())


def _list_preset_names() -> list[str]:
    """Return sorted list of preset names (excluding workflow files)."""
    if not PRESETS_DIR.exists():
        return []
    return sorted(
        p.stem
        for p in PRESETS_DIR.glob("*.json")
        if not p.stem.endswith("_workflow")
    )


def _saved_files(output: str, require_exists: bool = False) -> list[str]:
    """Extract absolute paths from 'Saved X' lines in comfy.sh output.

    If require_exists is True, filter out paths that don't exist on disk.
    Single source of truth for output parsing — used by both the JSON wrapper
    and the 'first existing file' helper.
    """
    if not output:
        return []
    files: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        if not s.startswith("Saved "):
            continue
        path = s.replace("Saved ", "").strip()
        full = COMFY_SH.parent / path
        if require_exists and not full.exists():
            continue
        files.append(str(full))
    return files


def _parse_saved_files(output: str) -> str:
    """JSON-structured result for MCP tool returns ('status', 'output', 'files', 'file')."""
    files = _saved_files(output)
    result: dict = {"status": "success", "output": output}
    if files:
        result["files"] = files
        result["file"] = files[-1]
    return json.dumps(result)


# ── MCP Resources ───────────────────────────────────────────────────────


@mcp.resource("comfy://presets")
def presets_index() -> str:
    """All available preset metadata including prompt guides and capabilities."""
    presets = {}
    for name in _list_preset_names():
        presets[name] = _load_preset(name)
    return json.dumps(presets, indent=2)


@mcp.resource("comfy://presets/{name}")
def preset_detail(name: str) -> str:
    """Detailed preset config including prompt_guide, capabilities, and output_format."""
    return json.dumps(_load_preset(name), indent=2)


# ── MCP Prompts ──────────────────────────────────────────────────────────


@mcp.prompt()
def creative_brief(brief: str) -> str:
    """Plan a multi-step creative pipeline from a high-level brief."""
    preset_names = _list_preset_names()
    return f"""You are a creative director planning a generation pipeline.

Brief: {brief}

Available presets: {', '.join(preset_names)}

Plan your approach:
1. Read the relevant preset resources (comfy://presets/{{name}}) to understand capabilities and prompt guides
2. Break the brief into generation steps (image gen → optional editing → optional animation)
3. For each step, specify: preset, prompt strategy, and success criteria
4. Execute each step, evaluate the output visually, and iterate if needed

Start by reading the preset resources you'll need, then execute the plan."""


@mcp.prompt()
def evaluate_generation(file_path: str, intent: str) -> str:
    """Evaluate a generated image/video against the original intent."""
    return f"""Evaluate this generation output.

File: {file_path}
Original intent: {intent}

Score each dimension 1-5:
- **Composition**: Layout, framing, visual balance
- **Style**: Matches the intended aesthetic
- **Quality**: Resolution, clarity, absence of artifacts
- **Fidelity**: How well it matches the prompt/intent

Based on scores:
- If all scores ≥ 4: Accept the output
- If any score ≤ 2: Identify the issue and suggest a specific prompt adjustment
- Otherwise: Consider retrying with a different seed or minor prompt tweak

Provide your evaluation and recommended next action."""


# ── MCP Tools: Generation ───────────────────────────────────────────────


@mcp.tool()
@_handle_errors
def comfy_generate(
    preset: str,
    prompt: str | None = None,
    seed: int | None = None,
    overrides: list[str] | None = None,
    output_dir: str | None = None,
) -> str:
    """Generate an image using a preset. Returns the file path of the output.

    Use comfy_list_presets first to see available presets, then read
    comfy://presets/{name} for the prompt guide before crafting your prompt.

    Args:
        preset: Preset name (e.g. "z-turbo").
        prompt: Text prompt for generation.
        seed: Specific seed for reproducibility. Omit for random.
        overrides: Extra node overrides as "node_id.field=value" strings.
            Use for fine-tuning workflow params beyond prompt/seed (e.g. steps, cfg, resolution).
        output_dir: Directory to save outputs. Defaults to downloads/.
    """
    args = ["gen", f"--preset={preset}"]
    if prompt:
        args.extend(["--prompt", prompt])
    if seed is not None:
        args.extend([f"--seed={seed}"])
    for override in overrides or []:
        args.extend(["--set", override])
    if output_dir:
        args.append(f"--out={output_dir}")
    output = _run_comfy(*args, timeout=600)
    return _parse_saved_files(output)


@mcp.tool()
@_handle_errors
def comfy_submit(
    preset: str,
    prompt: str | None = None,
    seed: int | None = None,
    image_path: str | None = None,
    overrides: list[str] | None = None,
) -> str:
    """Submit a generation job without waiting. Returns the job ID immediately.

    Use this for parallel generation — submit multiple jobs, then collect
    results with comfy_job_wait. Works with any preset type (txt2img, img2vid, edit).

    Args:
        preset: Preset name.
        prompt: Text prompt for generation.
        seed: Specific seed for reproducibility. Omit for random.
        image_path: Source image path (for img2vid/edit presets). Will be uploaded first.
        overrides: Extra node overrides as "node_id.field=value" strings.
    """
    p = _load_preset(preset)
    wf_file = str(PRESETS_DIR / p["workflow"])

    set_args = []
    # Set prompt
    prompt_node = p.get("prompt_node", "")
    prompt_field = p.get("prompt_field", "")
    if prompt and prompt_node and prompt_field:
        set_args.extend(["--set", f"{prompt_node}.{prompt_field}={prompt}"])
    # Set seed
    seed_node = p.get("seed_node", "")
    if seed is not None and seed_node:
        set_args.extend(["--set", f"{seed_node}.seed={seed}"])
    # Handle image upload
    if image_path:
        image_node = p.get("image_node", "")
        if image_node:
            upload_output = _run_comfy("upload", image_path)
            # Extract uploaded filename from output
            uploaded = upload_output.strip().splitlines()[-1].strip()
            set_args.extend(["--set", f"{image_node}.image={uploaded}"])
    for override in overrides or []:
        set_args.extend(["--set", override])

    output = _run_comfy("run-with", wf_file, *set_args, timeout=30)

    # Extract prompt_id from JSON response
    try:
        resp = json.loads(output)
        job_id = resp.get("prompt_id", "")
    except json.JSONDecodeError:
        job_id = ""

    if not job_id:
        return json.dumps({"status": "error", "error": "No job ID in response", "output": output})
    return json.dumps({"status": "submitted", "job_id": job_id})


@mcp.tool()
@_handle_errors
def comfy_animate(
    image_path: str,
    preset: str,
    prompt: str | None = None,
    seed: int | None = None,
    overrides: list[str] | None = None,
    output_dir: str | None = None,
) -> str:
    """Animate an image into a video using an img2vid preset.

    Uploads the image, runs the animation workflow, and returns the output video path.
    Suitable presets: wan22-i2v (high quality), ltx23-i2v (with audio).

    Args:
        image_path: Local path to the source image.
        preset: Preset name (e.g. "wan22-i2v", "ltx23-i2v").
        prompt: Motion/camera description. Read the preset's prompt_guide first.
        seed: Specific seed for reproducibility. Omit for random.
        overrides: Extra node overrides as "node_id.field=value" strings.
        output_dir: Directory to save outputs. Defaults to downloads/.
    """
    args = ["animate", image_path, f"--preset={preset}"]
    if prompt:
        args.extend(["--prompt", prompt])
    if seed is not None:
        args.extend([f"--seed={seed}"])
    for override in overrides or []:
        args.extend(["--set", override])
    if output_dir:
        args.append(f"--out={output_dir}")
    output = _run_comfy(*args, timeout=600)
    return _parse_saved_files(output)


@mcp.tool()
@_handle_errors
def comfy_batch_seed(
    preset: str,
    prompt: str,
    start_seed: int,
    end_seed: int,
    delay: int | None = None,
    overrides: list[str] | None = None,
) -> str:
    """Generate multiple variations by sweeping seed values.

    Useful for exploring different outputs from the same prompt.
    Returns a list of job IDs (use comfy_job_wait to collect results).

    Args:
        preset: Preset name.
        prompt: Text prompt applied to all variations.
        start_seed: First seed value (inclusive).
        end_seed: Last seed value (inclusive).
        delay: Seconds to wait between submissions (prevents rate limiting).
        overrides: Extra node overrides as "node_id.field=value" strings.
    """
    p = _load_preset(preset)
    seed_node = p.get("seed_node", "")
    if not seed_node:
        return json.dumps({"error": f"Preset '{preset}' has no seed_node"})

    wf_file = str(PRESETS_DIR / p["workflow"])

    # Build --set args for the prompt
    set_args = []
    prompt_node = p.get("prompt_node", "")
    prompt_field = p.get("prompt_field", "")
    if prompt_node and prompt_field:
        set_args.extend(["--set", f"{prompt_node}.{prompt_field}={prompt}"])
    for override in overrides or []:
        set_args.extend(["--set", override])
    if delay is not None:
        set_args.append(f"--delay={delay}")

    output = _run_comfy(
        "batch-seed", wf_file, seed_node, str(start_seed), str(end_seed),
        *set_args,
        timeout=120 + (end_seed - start_seed) * (delay or 0),
    )
    return json.dumps({"status": "submitted", "output": output})


# ── MCP Tools: Presets ───────────────────────────────────────────────────


@mcp.tool()
def comfy_list_presets() -> str:
    """List all available generation presets with descriptions and capabilities.

    Returns preset names, descriptions, capabilities (txt2img, img2vid, edit, multi-angle),
    and output formats. Read comfy://presets/{name} for full details including prompt guides.
    """
    result = {}
    for name in _list_preset_names():
        p = _load_preset(name)
        result[name] = {
            "description": p.get("description", ""),
            "capabilities": p.get("capabilities", []),
            "output_format": p.get("output_format", "png"),
            "has_prompt": bool(p.get("prompt_node")),
            "has_seed": bool(p.get("seed_node")),
            "has_image_input": bool(p.get("image_node")),
        }
    return json.dumps(result, indent=2)


# ── MCP Tools: Upload & Assets ───────────────────────────────────────────


@mcp.tool()
@_handle_errors
def comfy_upload_image(local_path: str) -> str:
    """Upload a local image to Comfy Cloud for use in workflows.

    Returns the uploaded filename which can be referenced in subsequent operations.
    """
    output = _run_comfy("upload", local_path)
    return json.dumps({"status": "success", "output": output})


@mcp.tool()
@_handle_errors
def comfy_asset_search(
    pattern: str,
    tag: str | None = None,
    limit: int | None = None,
) -> str:
    """Search uploaded assets by name pattern with optional filters.

    Args:
        pattern: Name pattern to search for (substring match).
        tag: Filter by asset tag (e.g. "input", "models").
        limit: Maximum number of results to return.
    """
    args = ["asset-search", pattern]
    if tag:
        args.append(f"--tag={tag}")
    if limit is not None:
        args.append(f"--limit={limit}")
    output = _run_comfy(*args)
    return json.dumps({"output": output})


# ── MCP Tools: Jobs ──────────────────────────────────────────────────────


@mcp.tool()
@_handle_errors
def comfy_job_list(
    status: str | None = None,
    limit: int | None = None,
) -> str:
    """List recent jobs with optional status filter.

    Args:
        status: Filter by status (e.g. "completed", "running", "pending", "failed").
        limit: Maximum number of jobs to return.
    """
    args = ["jobs"]
    if status:
        args.append(f"--status={status}")
    if limit is not None:
        args.append(f"--limit={limit}")
    output = _run_comfy(*args)
    return output


@mcp.tool()
@_handle_errors
def comfy_job_status(job_id: str) -> str:
    """Check the current status of a generation job."""
    output = _run_comfy("status", job_id)
    return output


@mcp.tool()
@_handle_errors
def comfy_job_wait(
    job_id: str,
    output_dir: str | None = None,
) -> str:
    """Poll a job until completion and download outputs.

    Blocks until the job finishes (success or failure).
    Returns downloaded file paths on success.

    Args:
        job_id: The job/prompt ID to wait for.
        output_dir: Directory to save outputs. Defaults to downloads/.
    """
    args = ["poll", job_id, "--download"]
    if output_dir:
        args.append(f"--out={output_dir}")
    output = _run_comfy(*args, timeout=600)
    return _parse_saved_files(output)


@mcp.tool()
@_handle_errors
def comfy_cancel_jobs(job_ids: list[str]) -> str:
    """Cancel one or more pending jobs.

    Args:
        job_ids: List of job/prompt IDs to cancel.
    """
    output = _run_comfy("cancel", *job_ids)
    return json.dumps({"status": "cancelled", "output": output})


@mcp.tool()
@_handle_errors
def comfy_download(
    filename: str,
    output_dir: str | None = None,
) -> str:
    """Download a specific output file from Comfy Cloud by filename.

    Use this to retrieve individual files from completed jobs. The filename
    comes from job detail output or comfy_job_wait results.

    Args:
        filename: The output filename on Comfy Cloud.
        output_dir: Local directory to save to. Defaults to downloads/.
    """
    args = ["download", filename]
    # download command doesn't support --out, so we handle it by
    # downloading to default location then moving if needed
    output = _run_comfy(*args)
    default_path = DOWNLOADS_DIR / filename
    if output_dir and default_path.exists():
        dest_dir = Path(output_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        default_path.rename(dest)
        return json.dumps({"status": "success", "file": str(dest), "output": output})
    return json.dumps({"status": "success", "file": str(default_path), "output": output})


# ── MCP Tools: Workflows ────────────────────────────────────────────────


@mcp.tool()
@_handle_errors
def comfy_run_workflow(
    workflow_path: str,
    overrides: list[str] | None = None,
    output_dir: str | None = None,
) -> str:
    """Run an arbitrary workflow JSON with optional node overrides.

    Submits the job, polls until completion, and downloads outputs.

    Args:
        workflow_path: Path to a ComfyUI workflow JSON (API format).
        overrides: Node overrides as "node_id.field=value" strings.
            Example: ["6.text=a sunset", "3.seed=42"]
        output_dir: Directory to save outputs. Defaults to downloads/.
    """
    args = ["go", workflow_path]
    for override in overrides or []:
        args.extend(["--set", override])
    if output_dir:
        args.append(f"--out={output_dir}")
    output = _run_comfy(*args, timeout=600)
    return _parse_saved_files(output)


# ── MCP Tools: Outputs ──────────────────────────────────────────────────


@mcp.tool()
def comfy_list_outputs(
    limit: int = 50,
    extension: str | None = None,
) -> str:
    """List recent files in the downloads directory.

    Args:
        limit: Maximum number of files to return (default 50).
        extension: Filter by file extension, e.g. "png", "mp4". Without dot.
    """
    if not DOWNLOADS_DIR.exists():
        return json.dumps({"files": []})
    files = sorted(DOWNLOADS_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        if not f.is_file():
            continue
        if extension and f.suffix.lstrip(".").lower() != extension.lower():
            continue
        result.append({
            "name": f.name,
            "path": str(f),
            "size_kb": round(f.stat().st_size / 1024, 1),
            "modified": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
        if len(result) >= limit:
            break
    return json.dumps({"files": result}, indent=2)


# ── MCP Tools: Project Management ────────────────────────────────────────


@mcp.tool()
def comfy_project_create(name: str, brief: str, tags: list[str] | None = None) -> str:
    """Create a new creative project to track multi-step generation workflows.

    Projects persist across sessions so you can resume work later.
    """
    project_dir = PROJECTS_DIR / name
    if project_dir.exists():
        return json.dumps({"error": f"Project '{name}' already exists"})

    project_dir.mkdir(parents=True)
    (project_dir / "outputs").mkdir()

    project = {
        "name": name,
        "brief": brief,
        "tags": tags or [],
        "created": datetime.datetime.now().isoformat(),
        "log": [],
    }
    (project_dir / "project.json").write_text(json.dumps(project, indent=2))
    return json.dumps({"status": "created", "path": str(project_dir)})


@mcp.tool()
def comfy_project_list() -> str:
    """List all creative projects."""
    if not PROJECTS_DIR.exists():
        return json.dumps({"projects": []})
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        pfile = d / "project.json"
        if pfile.exists():
            p = json.loads(pfile.read_text())
            projects.append({
                "name": p["name"],
                "brief": p["brief"],
                "tags": p.get("tags", []),
                "created": p.get("created", ""),
                "steps": len(p.get("log", [])),
            })
    return json.dumps({"projects": projects}, indent=2)


@mcp.tool()
def comfy_project_log(
    name: str,
    action: str,
    details: str,
    output_file: str | None = None,
    preset: str | None = None,
    prompt: str | None = None,
    seed: int | None = None,
    evaluation: str | None = None,
) -> str:
    """Log a generation step in a project.

    Call this after each generation to maintain a record of the creative process.
    If output_file is provided, a symlink is created in the project's outputs/ dir.

    Args:
        name: Project name.
        action: What was done (e.g. "generate", "animate", "edit", "evaluate").
        details: Free-text description of the step and reasoning.
        output_file: Path to the generated file (symlinked into project outputs/).
        preset: Preset used for this step (for reproducibility).
        prompt: Prompt used for this step (for reproducibility).
        seed: Seed used (for reproducibility).
        evaluation: Quality evaluation notes (e.g. scores, accept/retry decision).
    """
    project_dir = PROJECTS_DIR / name
    pfile = project_dir / "project.json"
    if not pfile.exists():
        return json.dumps({"error": f"Project '{name}' not found"})

    project = json.loads(pfile.read_text())
    entry: dict = {
        "timestamp": datetime.datetime.now().isoformat(),
        "action": action,
        "details": details,
    }

    if preset:
        entry["preset"] = preset
    if prompt:
        entry["prompt"] = prompt
    if seed is not None:
        entry["seed"] = seed
    if evaluation:
        entry["evaluation"] = evaluation

    if output_file:
        source = Path(output_file)
        if source.exists():
            link = project_dir / "outputs" / source.name
            if not link.exists():
                link.symlink_to(source.resolve())
            entry["output"] = str(link)

    project["log"].append(entry)
    pfile.write_text(json.dumps(project, indent=2))
    return json.dumps({"status": "logged", "entry": entry})


@mcp.tool()
def comfy_project_status(name: str) -> str:
    """Get full project state including brief, tags, and generation log."""
    pfile = PROJECTS_DIR / name / "project.json"
    if not pfile.exists():
        return json.dumps({"error": f"Project '{name}' not found"})
    return pfile.read_text()


# ── MCP Tools: Music Video — Creative Brief ──────────────────────────────


@mcp.tool()
def comfy_mv_set_brief(
    project_name: str,
    narrative: str | None = None,
    mood: str | None = None,
    visual_style: str | None = None,
    color_palette: str | None = None,
    camera_style: str | None = None,
    global_style: str | None = None,
    suggested_elements: list[dict] | None = None,
    suggested_scenes: list[dict] | None = None,
    notes: str | None = None,
) -> str:
    """Set the creative brief — Claude's artistic vision for the music video.

    Call this after reading the transcript from comfy_mv_plan. Analyze the
    lyrics, mood, and narrative arc, then set the full creative direction.

    When suggested_elements is provided, those elements are automatically
    created in the project (user can then upload source images or generate
    references for them).

    When suggested_scenes is provided, those descriptions are saved but not
    yet applied — use comfy_mv_set_prompts to finalize scene prompts after
    element references are approved.

    Args:
        project_name: Project directory name.
        narrative: Overall story arc (e.g. "A defiant musician fights back after police raid his home...").
        mood: Emotional tone (e.g. "defiant, humorous, triumphant").
        visual_style: Look and feel (e.g. "cinematic, Kodak 500T warm tones, dramatic lighting").
        color_palette: Dominant colors (e.g. "red white blue, gold, neon police lights").
        camera_style: Camera language (e.g. "mix of close-ups for lip sync and wide establishing shots").
        global_style: Applied to all generations.
        suggested_elements: List of {category, id, name, description} dicts. Auto-created as elements.
        suggested_scenes: List of {id, description, element_ids, visual, motion} dicts. Saved to brief.
        notes: Any other creative direction.
    """
    from .music_video import Storyboard, WorldElement

    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    sb = Storyboard.load(sb_path)

    # Update brief fields
    if narrative is not None:
        sb.brief.narrative = narrative
    if mood is not None:
        sb.brief.mood = mood
    if visual_style is not None:
        sb.brief.visual_style = visual_style
    if color_palette is not None:
        sb.brief.color_palette = color_palette
    if camera_style is not None:
        sb.camera_style = camera_style
    if global_style is not None:
        sb.global_style = global_style
    if notes is not None:
        sb.brief.notes = notes

    # Auto-create suggested elements
    created = 0
    if suggested_elements:
        sb.brief.suggested_elements = suggested_elements
        for el_data in suggested_elements:
            eid = el_data.get("id", "")
            if eid and not sb.get_element(eid):
                elements_dir = project_dir / "elements" / eid
                elements_dir.mkdir(parents=True, exist_ok=True)
                sb.elements.append(WorldElement(
                    id=eid,
                    category=el_data.get("category", "what"),
                    name=el_data.get("name", eid),
                    description=el_data.get("description", ""),
                ))
                created += 1

    if suggested_scenes:
        sb.brief.suggested_scenes = suggested_scenes

    sb.save(sb_path)

    return json.dumps({
        "status": "brief_set",
        "elements_created": created,
        "total_elements": len(sb.elements),
    })


@mcp.tool()
def comfy_mv_get_brief(project_name: str) -> str:
    """Get the creative brief and full transcript for a music video project.

    Returns the brief (narrative, mood, style, suggested elements/scenes)
    plus the raw transcript with timestamps. Use this to review the creative
    direction or to inform prompt writing.

    Args:
        project_name: Project directory name.
    """
    from .music_video import Storyboard
    from dataclasses import asdict

    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    sb = Storyboard.load(sb_path)

    return json.dumps({
        "title": sb.title,
        "duration": round(sb.duration, 1),
        "brief": asdict(sb.brief),
        "global_style": sb.global_style,
        "camera_style": sb.camera_style,
        "elements": [{"id": e.id, "category": e.category, "name": e.name,
                       "description": e.description,
                       "source_images": len(e.source_images),
                       "reference_images": len(e.reference_images)}
                      for e in sb.elements],
        "scenes": [{"id": s.id, "start": round(s.start, 1), "end": round(s.end, 1),
                     "duration": round(s.duration, 1), "text": s.text,
                     "segment_type": s.segment_type,
                     "has_prompt": bool(s.prompt),
                     "element_refs": s.element_refs}
                    for s in sb.scenes],
    }, indent=2)


# ── MCP Tools: Music Video — World Building ──────────────────────────────


@mcp.tool()
@_handle_errors
def comfy_mv_add_element(
    project_name: str,
    element_id: str,
    category: str,
    name: str,
    description: str,
    source_images: list[str] | None = None,
    seed: int = 42,
) -> str:
    """Add a world element (character, location, prop, mood) to the project.

    Elements are reusable across scenes. Each element can have:
    - User-provided source images (photos, sketches, references)
    - Generated reference images (created with comfy_mv_generate_element)
    - A detailed visual description for prompt composition

    Categories follow the 5W framework:
    - who: Characters and subjects
    - what: Actions, props, key objects
    - when: Time period, lighting, atmosphere
    - where: Locations and environments
    - why: Mood, emotion, theme

    Args:
        project_name: Project directory name.
        element_id: Unique slug (e.g. "afroman", "courtroom", "night-raid").
        category: One of: who, what, when, where, why.
        name: Display name (e.g. "Afroman", "The Courtroom").
        description: Detailed visual description for generation.
        source_images: Optional list of user-provided image paths.
        seed: Seed for reference image generation.
    """
    from .music_video import Storyboard, WorldElement

    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    sb = Storyboard.load(sb_path)

    # Check for duplicate
    if sb.get_element(element_id):
        return json.dumps({"error": f"Element '{element_id}' already exists"})

    # Copy source images to project
    elements_dir = project_dir / "elements" / element_id
    elements_dir.mkdir(parents=True, exist_ok=True)
    copied_sources = []
    for src in source_images or []:
        src_path = Path(src)
        if src_path.exists():
            import shutil
            dest = elements_dir / f"source_{src_path.name}"
            shutil.copy2(str(src_path), str(dest))
            copied_sources.append(str(dest))

    element = WorldElement(
        id=element_id,
        category=category,
        name=name,
        description=description,
        source_images=copied_sources,
        seed=seed,
    )
    sb.elements.append(element)
    sb.save(sb_path)

    return json.dumps({
        "status": "added",
        "element": element_id,
        "category": category,
        "source_images": len(copied_sources),
    })


@mcp.tool()
@_handle_errors
def comfy_mv_generate_element(
    project_name: str,
    element_id: str,
    prompt: str | None = None,
    use_source: bool = True,
    multi_angle: bool = False,
    seed: int | None = None,
) -> str:
    """Generate reference images for a world element.

    Three modes:
    1. Text-only: Generate from the element's description (no source images)
    2. Edit from source: Use qwen-edit to transform a source image based on instructions
    3. Multi-angle: Use multi-angles preset to generate 8 views from a source/generated image

    Generated images are saved as approved references for the element.
    View them to approve, then they'll be used in scene image generation.

    Args:
        project_name: Project directory name.
        element_id: Which element to generate references for.
        prompt: Override prompt. Defaults to element description.
        use_source: If True and source images exist, use qwen-edit from source. If False, generate from text.
        multi_angle: If True, generate 8 camera angles from the first reference/source image.
        seed: Override seed.
    """
    import shutil
    from .music_video import Storyboard

    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    sb = Storyboard.load(sb_path)
    element = sb.get_element(element_id)
    if not element:
        return json.dumps({"error": f"Element '{element_id}' not found"})

    elements_dir = project_dir / "elements" / element_id
    elements_dir.mkdir(parents=True, exist_ok=True)
    gen_seed = seed if seed is not None else element.seed
    gen_prompt = prompt or element.description

    results = []

    if multi_angle:
        # Need an existing image to create angles from
        source = None
        if element.reference_images:
            source = element.reference_images[0]
        elif element.source_images:
            source = element.source_images[0]
        if not source or not Path(source).exists():
            return json.dumps({"error": "multi_angle requires an existing reference or source image"})

        output = _run_comfy(
            "animate", source, "--preset=multi-angles",
            f"--seed={gen_seed}",
        )
        saved = _find_saved_file(output)
        if saved:
            dest = elements_dir / f"ref_angles_{gen_seed}.png"
            shutil.copy2(saved, str(dest))
            element.reference_images.append(str(dest))
            results.append(str(dest))

    elif use_source and element.source_images:
        # Use qwen-edit to transform source image
        source = element.source_images[0]
        if not Path(source).exists():
            return json.dumps({"error": f"Source image not found: {source}"})

        output = _run_comfy(
            "animate", source, "--preset=qwen-edit",
            "--prompt", gen_prompt,
            f"--seed={gen_seed}",
        )
        saved = _find_saved_file(output)
        if saved:
            dest = elements_dir / f"ref_edit_{gen_seed}.png"
            shutil.copy2(saved, str(dest))
            element.reference_images.append(str(dest))
            results.append(str(dest))

    else:
        # Generate from text with z-turbo
        # Category-specific framing
        framing = {
            "who": "Character portrait, medium close-up, face clearly visible",
            "what": "Dynamic shot, clear detail",
            "when": "Atmospheric establishing shot",
            "where": "Wide establishing shot, full environment visible",
            "why": "Abstract mood board, emotional atmosphere",
        }.get(element.category, "")

        full_prompt = f"{framing}, {gen_prompt}" if framing else gen_prompt
        output = _run_comfy(
            "gen", "--preset=z-turbo",
            "--prompt", full_prompt,
            f"--seed={gen_seed}",
            "--set", f"57:13.width={sb.width}",
            "--set", f"57:13.height={sb.height}",
        )
        saved = _find_saved_file(output)
        if saved:
            dest = elements_dir / f"ref_gen_{gen_seed}.png"
            shutil.copy2(saved, str(dest))
            element.reference_images.append(str(dest))
            results.append(str(dest))

    sb.save(sb_path)

    return json.dumps({
        "status": "generated",
        "element": element_id,
        "references": results,
        "total_references": len(element.reference_images),
    })


@mcp.tool()
def comfy_mv_list_elements(project_name: str) -> str:
    """List all world elements in a music video project with their reference status.

    Args:
        project_name: Project directory name.
    """
    from .music_video import Storyboard

    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    sb = Storyboard.load(sb_path)
    result = {}
    for e in sb.elements:
        result[e.id] = {
            "category": e.category,
            "name": e.name,
            "description": e.description[:100],
            "source_images": len(e.source_images),
            "reference_images": len(e.reference_images),
            "reference_paths": e.reference_images,
        }
    return json.dumps(result, indent=2)


@mcp.tool()
def comfy_mv_update_element(
    project_name: str,
    element_id: str,
    description: str | None = None,
    name: str | None = None,
    remove_reference: str | None = None,
) -> str:
    """Update a world element's description, name, or remove a reference image.

    Use after viewing generated references to refine the element.

    Args:
        project_name: Project directory name.
        element_id: Element to update.
        description: New visual description.
        name: New display name.
        remove_reference: Path of a reference image to remove (didn't look right).
    """
    from .music_video import Storyboard

    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    sb = Storyboard.load(sb_path)
    element = sb.get_element(element_id)
    if not element:
        return json.dumps({"error": f"Element '{element_id}' not found"})

    if description is not None:
        element.description = description
    if name is not None:
        element.name = name
    if remove_reference and remove_reference in element.reference_images:
        element.reference_images.remove(remove_reference)

    sb.save(sb_path)
    return json.dumps({
        "status": "updated",
        "element": element_id,
        "reference_count": len(element.reference_images),
    })


# ── MCP Tools: Music Video — Pipeline ───────────────────────────────────


@mcp.tool()
@_handle_errors
def comfy_mv_plan(
    audio_path: str,
    title: str,
    project_name: str,
    width: int = 1280,
    height: int = 720,
    min_duration: float = 5.0,
    max_duration: float = 30.0,
    lyrics: str | None = None,
) -> str:
    """Transcribe a song and build a music video storyboard with timed scenes.

    Two modes depending on whether lyrics are provided:

    1. **Lyrics mode** — pass `lyrics` (e.g. pasted from Genius with
       [Verse]/[Chorus] markers). Whisper is skipped entirely; sections
       become scenes, weighted by line count and beat-snapped via librosa.
       Right for instrumental / sample-heavy / pitched-vocal tracks where
       Whisper can't reliably detect structure.

    2. **Whisper mode** (default) — transcribes the audio and cuts scenes
       at natural transitions. Falls back to beat-based splits when any
       scene exceeds max_duration.

    After calling this, review the scenes and set prompts with comfy_mv_set_prompts.

    Args:
        audio_path: Path to the music file (mp3, wav, etc.).
        title: Title of the music video.
        project_name: Name for the project directory (under projects/).
        width: Video width in pixels (default 1280).
        height: Video height in pixels (default 720).
        min_duration: Minimum clip length guardrail (default 5s).
        max_duration: Maximum clip length guardrail (default 30s).
        lyrics: Optional full lyrics text. When provided, skips Whisper and
            structures scenes by [Section] markers.
    """
    from .music_video import plan, Storyboard

    sb, plan_info = plan(audio_path, title, min_duration, max_duration, lyrics=lyrics)
    sb.width = width
    sb.height = height

    # Save storyboard
    project_dir = PROJECTS_DIR / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ["scenes", "segments", "clips"]:
        (project_dir / subdir).mkdir(exist_ok=True)

    sb_path = project_dir / "storyboard.json"
    sb.save(sb_path)

    # Summary
    scenes_summary = []
    for s in sb.scenes:
        scenes_summary.append({
            "id": s.id,
            "start": round(s.start, 1),
            "end": round(s.end, 1),
            "duration": round(s.duration, 1),
            "text": s.text,
            "segment_type": s.segment_type,
            "prompt": s.prompt,
            "motion_prompt": s.motion_prompt,
            "element_refs": s.element_refs,
        })

    # Full transcript for Claude to analyze
    full_lyrics = "\n".join(
        f"[{s.start:.1f}s - {s.end:.1f}s] {s.text}" for s in sb.scenes if s.text
    )

    response = {
        "status": "planned",
        "project": str(project_dir),
        "storyboard": str(sb_path),
        "duration": round(sb.duration, 1),
        "scene_count": len(sb.scenes),
        "scenes": scenes_summary,
        "full_lyrics": full_lyrics,
        "next_step": "Call comfy_mv_set_brief with your creative analysis — narrative, mood, visual style, suggested elements (who/what/when/where/why), and suggested scenes. Then the user can upload reference images or approve auto-generation.",
    }
    if plan_info.get("beat_info"):
        response["beat_info"] = plan_info["beat_info"]
    if plan_info.get("warnings"):
        response["warnings"] = plan_info["warnings"]
    return json.dumps(response, indent=2)


@mcp.tool()
def comfy_mv_set_prompts(
    project_name: str,
    scene_prompts: list[dict],
) -> str:
    """Set visual and motion prompts for scenes in a music video storyboard.

    Each entry in scene_prompts should have:
      - id: scene ID number
      - prompt: visual description for the start frame image
      - motion_prompt: camera/motion description for animation
      - seed: (optional) specific seed for reproducibility

    You can also set world_elements and camera_style on the storyboard.

    Args:
        project_name: Project directory name.
        scene_prompts: List of {id, prompt, motion_prompt, seed?} dicts.
    """
    sb_path = PROJECTS_DIR / project_name / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    data = json.loads(sb_path.read_text())
    scenes_by_id = {s["id"]: s for s in data["scenes"]}

    updated = 0
    for sp in scene_prompts:
        sid = sp.get("id")
        if sid is not None and sid in scenes_by_id:
            scene = scenes_by_id[sid]
            if "prompt" in sp:
                scene["prompt"] = sp["prompt"]
            if "motion_prompt" in sp:
                scene["motion_prompt"] = sp["motion_prompt"]
            if "seed" in sp:
                scene["seed"] = sp["seed"]
            if "element_refs" in sp:
                scene["element_refs"] = sp["element_refs"]
            updated += 1

    sb_path.write_text(json.dumps(data, indent=2))

    # Soft-validate prompts against global_style and brief.visual_style —
    # surface contradictions rather than block them. Covers the common mistake
    # where a prompt ignores a constraint like "no chrome 3D render".
    constraint_text = " ".join([
        data.get("global_style") or "",
        (data.get("brief") or {}).get("visual_style") or "",
    ]).lower()
    conflicts = _style_conflicts(constraint_text, [
        (s["id"], (s.get("prompt") or "") + " " + (s.get("motion_prompt") or ""))
        for s in data["scenes"]
    ])

    out = {"status": "updated", "scenes_updated": updated}
    if conflicts:
        out["warnings"] = [{
            "type": "prompt_vs_style_conflict",
            "message": "Some scene prompts appear to contradict the project's global_style / visual_style (e.g. global says 'no X', scene prompt says 'X'). Review before generating.",
            "conflicts": conflicts,
        }]
    return json.dumps(out, indent=2)


@mcp.tool()
@_handle_errors
def comfy_mv_set_shots(
    project_name: str,
    scene_id: int,
    shots: list[dict],
) -> str:
    """Set the shot list for a scene — internal cuts that make the edit cut faster.

    A scene is a narrative tent-pole tied to a lyric section (e.g. a verse),
    usually 10-30s. By default it produces one image and one a2v clip filling
    that whole window, which feels static against hyperactive music. Attach
    shots to cut within the scene — alternate camera angles and cutaways —
    so the edit matches the song's energy.

    Each shot entry must have:
      - id (str): short slug unique within the scene, e.g. "a", "b", "broll1"
      - type (str): "lipsync" (ltx23-a2v, consumes a slice of the scene's
        audio — use for alternate angles of a performer) OR
        "broll" (wan22-i2v, silent — use for cutaways, props, environment)
      - duration (float): target length in seconds (recommended 2-8s)
      - prompt (str): visual prompt for the shot's start frame
      - motion_prompt (str, optional): camera/motion description for animation
      - element_refs (list[str|dict], optional): element IDs; falls back to
        the scene's element_refs when omitted
      - seed (int, optional): shot-specific seed

    Shots are played back in the order given. Their durations should sum to
    approximately the scene duration — the final video length is the sum of
    all shot durations plus any scenes without shots. Passing an empty list
    clears shots, restoring the 1-clip-per-scene default.

    Args:
        project_name: Project directory name.
        scene_id: Target scene ID.
        shots: Ordered list of shot specs.
    """
    sb_path = PROJECTS_DIR / project_name / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    data = json.loads(sb_path.read_text())
    scene = next((s for s in data["scenes"] if s["id"] == scene_id), None)
    if scene is None:
        return json.dumps({"error": f"Scene {scene_id} not found"})

    scene_duration = scene["end"] - scene["start"]
    warnings = []

    # Validate shots
    normalized = []
    seen_ids = set()
    total_duration = 0.0
    for i, shot in enumerate(shots):
        sid = str(shot.get("id", "")).strip()
        if not sid:
            return json.dumps({"error": f"Shot index {i} missing 'id'"})
        if sid in seen_ids:
            return json.dumps({"error": f"Duplicate shot id '{sid}' in scene {scene_id}"})
        seen_ids.add(sid)

        stype = shot.get("type", "lipsync")
        if stype not in ("lipsync", "broll"):
            return json.dumps({"error": f"Shot '{sid}' has invalid type '{stype}' (want 'lipsync' or 'broll')"})

        dur = float(shot.get("duration", 0))
        if dur <= 0:
            return json.dumps({"error": f"Shot '{sid}' duration must be > 0"})
        if stype == "lipsync" and dur > 30:
            warnings.append(f"Shot '{sid}' lipsync duration {dur}s exceeds a2v sweet spot (>30s degrades).")
        if stype == "broll" and dur > 8:
            warnings.append(f"Shot '{sid}' broll duration {dur}s is long for wan22-i2v; consider splitting.")
        total_duration += dur

        normalized.append({
            "id": sid,
            "type": stype,
            "duration": dur,
            "prompt": shot.get("prompt", ""),
            "motion_prompt": shot.get("motion_prompt", ""),
            "element_refs": shot.get("element_refs") or scene.get("element_refs") or [],
            "seed": int(shot.get("seed", scene.get("seed", 42 + scene_id) + i)),
            "image_path": "",
            "audio_path": "",
            "video_path": "",
        })

    if shots and abs(total_duration - scene_duration) > 2.0:
        warnings.append(
            f"Shot durations sum to {total_duration:.1f}s but scene is {scene_duration:.1f}s "
            f"— stitched video will be {total_duration - scene_duration:+.1f}s vs scene window."
        )

    scene["shots"] = normalized
    sb_path.write_text(json.dumps(data, indent=2))

    out = {
        "status": "updated",
        "scene_id": scene_id,
        "shot_count": len(normalized),
        "total_shot_duration": round(total_duration, 2),
        "scene_duration": round(scene_duration, 2),
    }
    if warnings:
        out["warnings"] = warnings
    return json.dumps(out, indent=2)


_NEG_PATTERN = re.compile(
    r"\b(?:no|without|avoid|never|not)\s+([a-z][a-z0-9 \-]{1,40}?)(?=[\.,;:!?\)]|\band\b|\bor\b|\bbut\b|$)",
    re.IGNORECASE,
)


def _style_conflicts(constraint_text: str, scenes: list[tuple]) -> list[dict]:
    """Find scenes whose prompt text contains phrases forbidden by the constraint text.

    Parses negative constraints like 'no chrome 3D', 'without text',
    'avoid typography' and flags scenes that mention those phrases.
    Deliberately conservative — keyword substring match, not NLP.
    """
    forbidden: list[str] = []
    for m in _NEG_PATTERN.finditer(constraint_text):
        phrase = m.group(1).strip().strip(",.")
        # Drop trivial stopwords-only matches
        if len(phrase) >= 3 and phrase not in {"one", "two", "the", "any", "all", "only"}:
            forbidden.append(phrase)

    if not forbidden:
        return []

    conflicts: list[dict] = []
    for sid, prompt_text in scenes:
        lower = prompt_text.lower()
        hit = [p for p in forbidden if p in lower]
        if hit:
            conflicts.append({"scene_id": sid, "forbidden_phrases_present": hit})
    return conflicts


_CATEGORY_PRIORITY = {"who": 0, "where": 1, "what": 2, "when": 3, "why": 4}


def _collect_scene_refs(
    scene: dict,
    elements_by_id: dict,
    max_refs: int = 3,
) -> list[str]:
    """Return up to max_refs reference image paths for a scene's element_refs.

    Priority order: who > where > what > when > why. Uses the first existing
    reference_image per element (falling back to source_images). Non-existent
    paths are skipped. Deduplicates identical paths across elements.
    """
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for ref in scene.get("element_refs", []) or []:
        eid = ref["element_id"] if isinstance(ref, dict) else ref
        el = elements_by_id.get(eid)
        if not el:
            continue
        candidates = list(el.get("reference_images") or []) + list(el.get("source_images") or [])
        for p in candidates:
            if not p or p in seen:
                continue
            if not Path(p).exists():
                continue
            seen.add(p)
            ranked.append((_CATEGORY_PRIORITY.get(el.get("category", ""), 99), p))
            break
    ranked.sort(key=lambda x: x[0])
    return [p for _, p in ranked[:max_refs]]


@mcp.tool()
@_handle_errors
def comfy_mv_generate(
    project_name: str,
    scenes: list[int] | None = None,
    step: str | None = None,
) -> str:
    """Generate music video assets — scene images, audio segments, and video clips.

    Runs the full pipeline or specific steps. Skips already-completed work
    (resume-safe). Use 'scenes' to regenerate specific scenes only.

    Image generation is ref-aware: if a scene (or shot) has element_refs with
    reference_images (generated via comfy_mv_generate_element), composition
    goes through Qwen-Image-Edit 2509 with up to 3 refs simultaneously —
    preserving character / location / prop identity across clips. Items
    with no refs fall back to z-turbo txt2img.

    When a scene has `shots` (set via comfy_mv_set_shots), generation operates
    at the shot level: one image + one clip per shot, with audio sliced from
    the scene's window for lipsync shots (ltx23-a2v) and silent wan22-i2v
    clips for broll shots. Scenes without shots use the default 1-clip behavior.

    Args:
        project_name: Project directory name.
        scenes: Optional list of scene IDs to generate. None = all scenes.
        step: Optional step to run: "images", "audio", "clips", or None for all.
    """
    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    data = json.loads(sb_path.read_text())
    all_scenes = data["scenes"]
    audio_path = data["audio_path"]
    vid_w = data.get("width", 1280)
    vid_h = data.get("height", 720)

    # Ensure output subdirs exist (resilient to user cleanup between runs)
    for subdir in ("scenes", "segments", "clips"):
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Filter to requested scenes
    if scenes is not None:
        target_scenes = [s for s in all_scenes if s["id"] in scenes]
        # Only clear files for the step being requested — regenerating clips
        # must not blow away the images / audio they depend on.
        ext_by_key = {"image_path": "png", "audio_path": "wav", "video_path": "mp4"}
        subdir_by_key = {"image_path": "scenes", "audio_path": "segments", "video_path": "clips"}
        step_to_key = {"images": "image_path", "audio": "audio_path", "clips": "video_path"}
        if step is None:
            keys_to_clear = list(ext_by_key.keys())
        else:
            key = step_to_key.get(step)
            keys_to_clear = [key] if key else []
        for s in target_scenes:
            s_shots = s.get("shots") or []
            if s_shots:
                for shot in s_shots:
                    stem = _shot_file_stem(s["id"], shot["id"])
                    for key in keys_to_clear:
                        path = project_dir / subdir_by_key[key] / f"{stem}.{ext_by_key[key]}"
                        if path.exists():
                            path.unlink()
                        shot[key] = ""
            else:
                for key in keys_to_clear:
                    path = project_dir / subdir_by_key[key] / f"scene_{s['id']:03d}.{ext_by_key[key]}"
                    if path.exists():
                        path.unlink()
                    s[key] = ""
    else:
        target_scenes = all_scenes

    # Element readiness check — elements referenced by multiple scenes without
    # a reference image will drift (different face / body per scene).
    warnings = []
    elements_by_id = {e["id"]: e for e in data.get("elements", [])}
    scene_refs_count: dict[str, int] = {}
    for s in target_scenes:
        for ref in s.get("element_refs", []) or []:
            eid = ref["element_id"] if isinstance(ref, dict) else ref
            scene_refs_count[eid] = scene_refs_count.get(eid, 0) + 1
    drifting = []
    for eid, count in scene_refs_count.items():
        if count < 2:
            continue
        el = elements_by_id.get(eid)
        if el is None:
            continue
        if not el.get("reference_images") and not el.get("source_images"):
            drifting.append({"id": eid, "name": el.get("name", eid), "scene_count": count})
    if drifting:
        warnings.append({
            "type": "element_drift",
            "message": "Elements appear in multiple scenes without reference images — identity/appearance will drift across clips. Run comfy_mv_generate_element first for each.",
            "elements": drifting,
        })

    results = {"images": 0, "audio": 0, "clips": 0, "failed": []}
    elements_by_id_for_gen = {e["id"]: e for e in data.get("elements", [])}

    def _shot_absolute_start(scene_dict: dict, shot_index: int) -> float:
        """Absolute timestamp (in the full song) where a scene's Nth shot begins."""
        offset = sum(float(s.get("duration", 0)) for s in scene_dict["shots"][:shot_index])
        return float(scene_dict["start"]) + offset

    # Step: Generate images
    if step in (None, "images"):
        for scene in target_scenes:
            scene_shots = scene.get("shots") or []
            if scene_shots:
                # Shot-level image generation
                for shot in scene_shots:
                    if not shot.get("prompt"):
                        continue
                    stem = _shot_file_stem(scene["id"], shot["id"])
                    img_path = project_dir / "scenes" / f"{stem}.png"
                    if img_path.exists():
                        shot["image_path"] = str(img_path)
                        continue
                    ok = _compose_image(
                        prompt=shot["prompt"],
                        element_refs=shot.get("element_refs") or scene.get("element_refs") or [],
                        elements_by_id=elements_by_id_for_gen,
                        vid_w=vid_w,
                        vid_h=vid_h,
                        seed=shot.get("seed", 42),
                        output_path=img_path,
                    )
                    if ok:
                        shot["image_path"] = str(img_path)
                        results["images"] += 1
                    else:
                        results["failed"].append({"scene": scene["id"], "shot": shot["id"], "step": "image"})
                continue

            # Scene-level (no shots) — original behavior
            if not scene.get("prompt"):
                continue
            img_path = project_dir / "scenes" / f"scene_{scene['id']:03d}.png"
            if img_path.exists():
                scene["image_path"] = str(img_path)
                continue
            ok = _compose_image(
                prompt=scene["prompt"],
                element_refs=scene.get("element_refs") or [],
                elements_by_id=elements_by_id_for_gen,
                vid_w=vid_w,
                vid_h=vid_h,
                seed=scene.get("seed", 42 + scene["id"]),
                output_path=img_path,
            )
            if ok:
                scene["image_path"] = str(img_path)
                results["images"] += 1
            else:
                results["failed"].append({"scene": scene["id"], "step": "image"})

    # Step: Split audio
    if step in (None, "audio"):
        seg_dir = project_dir / "segments"
        for scene in target_scenes:
            scene_shots = scene.get("shots") or []
            if scene_shots:
                # Shot-level audio — only lipsync shots get audio slices
                for i, shot in enumerate(scene_shots):
                    if shot.get("type") != "lipsync":
                        continue
                    stem = _shot_file_stem(scene["id"], shot["id"])
                    seg_path = seg_dir / f"{stem}.wav"
                    if seg_path.exists():
                        shot["audio_path"] = str(seg_path)
                        continue
                    abs_start = _shot_absolute_start(scene, i)
                    ok = _slice_audio(audio_path, abs_start, float(shot["duration"]), seg_path)
                    if ok:
                        shot["audio_path"] = str(seg_path)
                        results["audio"] += 1
                    else:
                        results["failed"].append({"scene": scene["id"], "shot": shot["id"], "step": "audio"})
                continue

            # Scene-level (no shots) — original behavior
            seg_path = seg_dir / f"scene_{scene['id']:03d}.wav"
            if seg_path.exists():
                scene["audio_path"] = str(seg_path)
                continue
            duration = scene["end"] - scene["start"]
            if _slice_audio(audio_path, float(scene["start"]), duration, seg_path):
                scene["audio_path"] = str(seg_path)
                results["audio"] += 1

    # Step: Generate video clips
    if step in (None, "clips"):
        for scene in target_scenes:
            scene_shots = scene.get("shots") or []
            if scene_shots:
                # Shot-level clip generation
                for shot in scene_shots:
                    stem = _shot_file_stem(scene["id"], shot["id"])
                    clip_path = project_dir / "clips" / f"{stem}.mp4"
                    if clip_path.exists():
                        shot["video_path"] = str(clip_path)
                        continue
                    if not shot.get("image_path"):
                        results["failed"].append({"scene": scene["id"], "shot": shot["id"], "step": "clip", "reason": "missing image"})
                        continue
                    seed = int(shot.get("seed", 42))
                    duration = float(shot["duration"])
                    motion = shot.get("motion_prompt") or "cinematic motion"
                    if shot.get("type") == "lipsync":
                        if not shot.get("audio_path"):
                            results["failed"].append({"scene": scene["id"], "shot": shot["id"], "step": "clip", "reason": "missing audio"})
                            continue
                        ok = _generate_a2v_clip(
                            shot["image_path"], shot["audio_path"], motion, duration, seed, clip_path
                        )
                    else:
                        ok = _generate_i2v_clip(
                            shot["image_path"], motion, duration, seed, vid_w, vid_h, clip_path
                        )
                    if ok:
                        shot["video_path"] = str(clip_path)
                        results["clips"] += 1
                    else:
                        results["failed"].append({"scene": scene["id"], "shot": shot["id"], "step": "clip"})
                continue

            # Scene-level (no shots) — original behavior
            clip_path = project_dir / "clips" / f"scene_{scene['id']:03d}.mp4"
            if clip_path.exists():
                scene["video_path"] = str(clip_path)
                continue
            if not scene.get("image_path") or not scene.get("audio_path"):
                results["failed"].append({"scene": scene["id"], "step": "clip", "reason": "missing assets"})
                continue
            duration = scene["end"] - scene["start"]
            ok = _generate_a2v_clip(
                scene["image_path"],
                scene["audio_path"],
                scene.get("motion_prompt", "cinematic motion"),
                duration,
                scene.get("seed", 42 + scene["id"]),
                clip_path,
            )
            if ok:
                scene["video_path"] = str(clip_path)
                results["clips"] += 1
            else:
                results["failed"].append({"scene": scene["id"], "step": "clip"})

    # Save progress
    data["scenes"] = all_scenes
    sb_path.write_text(json.dumps(data, indent=2))

    out = {
        "status": "generated",
        **results,
        "total_scenes": len(target_scenes),
    }
    if warnings:
        out["warnings"] = warnings
    return json.dumps(out, indent=2)


@mcp.tool()
@_handle_errors
def comfy_mv_stitch(
    project_name: str,
    output_filename: str | None = None,
) -> str:
    """Stitch all video clips into the final music video with the original audio.

    Concatenates clips in scene order, scales to consistent 1280x720,
    and overlays the original music track.

    Args:
        project_name: Project directory name.
        output_filename: Output filename. Defaults to '{title}_Music_Video.mp4'.
    """
    import subprocess as sp
    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    data = json.loads(sb_path.read_text())
    scenes = data["scenes"]
    audio_path = data["audio_path"]
    title = data.get("title", project_name)
    vid_w = data.get("width", 1280)
    vid_h = data.get("height", 720)

    if not output_filename:
        safe_title = title.replace(" ", "_").replace("'", "")
        output_filename = f"{safe_title}_Music_Video.mp4"
    output_path = project_dir / output_filename

    # Build concat file — walk scenes in order, expanding into shots where present.
    # Each clip is paired with its duration so the ffmpeg concat demuxer honors
    # the intended cut rhythm even when underlying clips drift by a frame.
    concat_file = project_dir / "concat.txt"
    lines = []
    valid = 0
    for scene in scenes:
        scene_shots = scene.get("shots") or []
        if scene_shots:
            for shot in scene_shots:
                vp = shot.get("video_path", "")
                if vp and Path(vp).exists():
                    escaped = vp.replace("'", "'\\''")
                    lines.append(f"file '{escaped}'")
                    lines.append(f"duration {float(shot.get('duration', 0)):.3f}")
                    valid += 1
        else:
            vp = scene.get("video_path", "")
            if vp and Path(vp).exists():
                escaped = vp.replace("'", "'\\''")
                lines.append(f"file '{escaped}'")
                duration = scene["end"] - scene["start"]
                lines.append(f"duration {duration:.3f}")
                valid += 1

    if not lines:
        return json.dumps({"error": "No valid clips to stitch"})
    concat_file.write_text("\n".join(lines))

    # Concat video
    temp = project_dir / "temp_video.mp4"
    sp.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-vf", f"scale={vid_w}:{vid_h}:force_original_aspect_ratio=decrease,pad={vid_w}:{vid_h}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-an", str(temp),
    ], capture_output=True)

    # Overlay original audio
    sp.run([
        "ffmpeg", "-y",
        "-i", str(temp), "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "320k",
        "-map", "0:v", "-map", "1:a",
        "-shortest", str(output_path),
    ], capture_output=True)

    temp.unlink(missing_ok=True)
    concat_file.unlink(missing_ok=True)

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        return json.dumps({
            "status": "complete",
            "output": str(output_path),
            "size_mb": round(size_mb, 1),
            "clips_used": valid,
            "total_scenes": len(scenes),
        })
    return json.dumps({"error": "Stitch failed — output not created"})


@mcp.tool()
def comfy_mv_status(project_name: str) -> str:
    """Check music video project status — what's done, pending, and failed.

    Args:
        project_name: Project directory name.
    """
    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    data = json.loads(sb_path.read_text())
    scenes = data["scenes"]

    # Aggregate status for both scene-level and shot-level assets. Shots roll up
    # into parent-scene counts so callers don't need to know which mode a scene
    # is running in.
    def _scene_image_ok(s: dict) -> bool:
        shots = s.get("shots") or []
        if shots:
            return all(sh.get("image_path") and Path(sh["image_path"]).exists() for sh in shots)
        return bool(s.get("image_path") and Path(s["image_path"]).exists())

    def _scene_audio_ok(s: dict) -> bool:
        shots = s.get("shots") or []
        if shots:
            lipsync = [sh for sh in shots if sh.get("type") == "lipsync"]
            if not lipsync:
                return True  # no lipsync shots means no audio needed
            return all(sh.get("audio_path") and Path(sh["audio_path"]).exists() for sh in lipsync)
        return bool(s.get("audio_path") and Path(s["audio_path"]).exists())

    def _scene_clips_ok(s: dict) -> bool:
        shots = s.get("shots") or []
        if shots:
            return all(sh.get("video_path") and Path(sh["video_path"]).exists() for sh in shots)
        return bool(s.get("video_path") and Path(s["video_path"]).exists())

    total_shots = sum(len(s.get("shots") or []) for s in scenes)
    status = {
        "title": data.get("title", ""),
        "duration": round(data.get("duration", 0), 1),
        "total_scenes": len(scenes),
        "total_shots": total_shots,
        "scenes_with_shots": sum(1 for s in scenes if s.get("shots")),
        "has_prompts": sum(1 for s in scenes if s.get("prompt")),
        "has_images": sum(1 for s in scenes if _scene_image_ok(s)),
        "has_audio": sum(1 for s in scenes if _scene_audio_ok(s)),
        "has_clips": sum(1 for s in scenes if _scene_clips_ok(s)),
        "missing_prompts": [s["id"] for s in scenes if not s.get("prompt")],
        "missing_clips": [s["id"] for s in scenes if not _scene_clips_ok(s)],
    }

    output_candidates = list(project_dir.glob("*_Music_Video.mp4"))
    if output_candidates:
        latest = max(output_candidates, key=lambda p: p.stat().st_mtime)
        status["output"] = str(latest)
        status["output_size_mb"] = round(latest.stat().st_size / (1024 * 1024), 1)

    return json.dumps(status, indent=2)


# ── Helpers for music video ─────────────────────────────────────────────


def _find_saved_file(output: str) -> str | None:
    """First 'Saved X' path that exists on disk, or None."""
    files = _saved_files(output, require_exists=True)
    return files[0] if files else None


def _upload_file(local_path: str) -> str | None:
    """Upload a file and return the remote filename."""
    output = _run_comfy("upload", local_path)
    if not output:
        return None
    try:
        data = json.loads(output)
        return data.get("name", "")
    except json.JSONDecodeError:
        return output.strip().splitlines()[-1].strip()


# ── Shot-level helpers ───────────────────────────────────────────────────


def _shot_file_stem(scene_id: int, shot_id: str) -> str:
    """Filename stem for a shot — e.g. scene_003_shot_b."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(shot_id))
    return f"scene_{scene_id:03d}_shot_{safe}"


def _compose_image(
    prompt: str,
    element_refs: list,
    elements_by_id: dict,
    vid_w: int,
    vid_h: int,
    seed: int,
    output_path,
) -> bool:
    """Generate a start-frame image — qwen-edit-Nref if refs exist, else z-turbo.

    Mirrors the scene-level image generation logic so shots and scenes share
    one composition path. Returns True on success (writes to output_path).
    Swallows ComfyError so a single transient cloud failure doesn't abort
    an entire batch — the caller logs the failure and moves on.
    """
    import shutil
    fake_scene = {"element_refs": element_refs}
    ref_paths = _collect_scene_refs(fake_scene, elements_by_id, max_refs=3)

    try:
        if not ref_paths:
            output = _run_comfy(
                "gen", "--preset=z-turbo",
                "--prompt", prompt,
                f"--seed={seed}",
                "--set", f"57:13.width={vid_w}",
                "--set", f"57:13.height={vid_h}",
            )
        else:
            uploaded = []
            for p in ref_paths:
                remote = _upload_file(p)
                if not remote:
                    return False
                uploaded.append(remote)
            n = len(uploaded)
            if n == 1:
                wf = "presets/qwen-edit_workflow.json"
                image_sets = [("78", uploaded[0])]
            elif n == 2:
                wf = "presets/qwen-edit-2ref_workflow.json"
                image_sets = [("78", uploaded[0]), ("79", uploaded[1])]
            else:
                wf = "presets/qwen-edit-3ref_workflow.json"
                image_sets = [("78", uploaded[0]), ("79", uploaded[1]), ("80", uploaded[2])]
            cmd = [
                "go", wf,
                "--set", f"435.value={prompt}",
                "--set", f"433:3.seed={seed}",
            ]
            for node_id, remote_name in image_sets:
                cmd.extend(["--set", f"{node_id}.image={remote_name}"])
            output = _run_comfy(*cmd)
    except ComfyError:
        return False

    saved = _find_saved_file(output)
    if not saved:
        return False
    shutil.copy2(saved, str(output_path))
    return True


def _slice_audio(audio_path: str, start: float, duration: float, output_path) -> bool:
    """Extract a mono 44.1kHz WAV segment from audio_path."""
    import subprocess as sp
    r = sp.run([
        "ffmpeg", "-y", "-i", audio_path,
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-ar", "44100", "-ac", "1", str(output_path),
    ], capture_output=True)
    return r.returncode == 0 and Path(output_path).exists()


def _generate_a2v_clip(
    image_path: str,
    audio_path: str,
    motion_prompt: str,
    duration: float,
    seed: int,
    output_path,
) -> bool:
    """Run ltx23-a2v — image + audio slice → lip-synced clip.

    Returns False on any failure (upload, cloud worker death, missing output).
    The caller is responsible for logging the shot id and letting the batch
    continue. This makes big generations resilient to Comfy Cloud's transient
    'RIP to the server your workflow was running on' errors.
    """
    import shutil
    try:
        img_remote = _upload_file(image_path)
        audio_remote = _upload_file(audio_path)
        if not img_remote or not audio_remote:
            return False
        output = _run_comfy(
            "go", "presets/ltx23-a2v_workflow.json",
            "--set", f"269.image={img_remote}",
            "--set", f"276.audio={audio_remote}",
            "--set", f"340:319.value={motion_prompt or 'cinematic motion'}",
            "--set", f"340:331.value={duration}",
            "--set", f"340:285.noise_seed={seed}",
            timeout=600,
        )
    except ComfyError:
        return False
    saved = _find_saved_file(output)
    if not saved:
        return False
    shutil.copy2(saved, str(output_path))
    return True


def _generate_i2v_clip(
    image_path: str,
    motion_prompt: str,
    duration: float,
    seed: int,
    width: int,
    height: int,
    output_path,
) -> bool:
    """Run wan22-i2v — image + motion prompt → silent video clip (for broll cutaways).

    Returns False on any failure (upload, cloud worker death, missing output)
    so batch generation can log the single miss and continue.
    """
    import shutil
    try:
        img_remote = _upload_file(image_path)
        if not img_remote:
            return False
        # wan22-i2v runs at 16fps internally; length is frames.
        length = max(16, int(round(duration * 16)))
        output = _run_comfy(
            "go", "presets/wan22-i2v_workflow.json",
            "--set", f"97.image={img_remote}",
            "--set", f"129:93.text={motion_prompt or 'subtle camera movement'}",
            "--set", f"129:86.noise_seed={seed}",
            "--set", f"129:98.length={length}",
            "--set", f"129:98.width={width}",
            "--set", f"129:98.height={height}",
            timeout=600,
        )
    except ComfyError:
        return False
    saved = _find_saved_file(output)
    if not saved:
        return False
    shutil.copy2(saved, str(output_path))
    return True


# ── Entry point ──────────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
