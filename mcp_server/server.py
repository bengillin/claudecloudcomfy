"""Comfy Cloud MCP Server — gives Claude hands for creative generation."""

import functools
import json
import os
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


def _parse_saved_files(output: str) -> str:
    """Parse comfy.sh output for 'Saved ...' lines and return structured JSON."""
    lines = output.splitlines()
    saved = [l.strip() for l in lines if l.strip().startswith("Saved ")]
    files = []
    for s in saved:
        path = s.replace("Saved ", "").strip()
        files.append(str(COMFY_SH.parent / path))
    result = {"status": "success", "output": output}
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
) -> str:
    """Transcribe a song and build a music video storyboard with timed scenes.

    Uses Whisper to transcribe the audio, then cuts scenes at natural
    transitions — segment type changes, silence gaps, mood shifts.
    Clip lengths vary based on what the song needs (5s min, 30s max guardrails).

    After calling this, review the scenes and set prompts with comfy_mv_set_prompts.

    Args:
        audio_path: Path to the music file (mp3, wav, etc.).
        title: Title of the music video.
        project_name: Name for the project directory (under projects/).
        width: Video width in pixels (default 1280).
        height: Video height in pixels (default 720).
        min_duration: Minimum clip length guardrail (default 5s).
        max_duration: Maximum clip length guardrail (default 30s).
    """
    from .music_video import plan, Storyboard

    sb = plan(audio_path, title, min_duration, max_duration)
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
            "text": s.text[:80],
            "segment_type": s.segment_type,
        })

    # Full transcript for Claude to analyze
    full_lyrics = "\n".join(
        f"[{s.start:.1f}s - {s.end:.1f}s] {s.text}" for s in sb.scenes if s.text
    )

    return json.dumps({
        "status": "planned",
        "project": str(project_dir),
        "storyboard": str(sb_path),
        "duration": round(sb.duration, 1),
        "scene_count": len(sb.scenes),
        "scenes": scenes_summary,
        "full_lyrics": full_lyrics,
        "next_step": "Call comfy_mv_set_brief with your creative analysis — narrative, mood, visual style, suggested elements (who/what/when/where/why), and suggested scenes. Then the user can upload reference images or approve auto-generation.",
    }, indent=2)


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
    return json.dumps({"status": "updated", "scenes_updated": updated})


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

    Args:
        project_name: Project directory name.
        scenes: Optional list of scene IDs to generate. None = all scenes.
        step: Optional step to run: "images", "audio", "clips", or None for all.
    """
    import shutil
    project_dir = PROJECTS_DIR / project_name
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return json.dumps({"error": f"Project '{project_name}' not found"})

    data = json.loads(sb_path.read_text())
    all_scenes = data["scenes"]
    audio_path = data["audio_path"]
    vid_w = data.get("width", 1280)
    vid_h = data.get("height", 720)

    # Filter to requested scenes
    if scenes is not None:
        target_scenes = [s for s in all_scenes if s["id"] in scenes]
        # Clear existing files so they get regenerated
        for s in target_scenes:
            for key, subdir in [("image_path", "scenes"), ("audio_path", "segments"), ("video_path", "clips")]:
                path = project_dir / subdir / f"scene_{s['id']:03d}.{'png' if key == 'image_path' else 'wav' if key == 'audio_path' else 'mp4'}"
                if path.exists():
                    path.unlink()
                s[key] = ""
    else:
        target_scenes = all_scenes

    results = {"images": 0, "audio": 0, "clips": 0, "failed": []}
    WIDTH, HEIGHT = 1280, 720

    # Step: Generate images
    if step in (None, "images"):
        for scene in target_scenes:
            if not scene.get("prompt"):
                continue
            img_path = project_dir / "scenes" / f"scene_{scene['id']:03d}.png"
            if img_path.exists():
                scene["image_path"] = str(img_path)
                continue
            output = _run_comfy(
                "gen", "--preset=z-turbo",
                "--prompt", scene["prompt"],
                f"--seed={scene.get('seed', 42 + scene['id'])}",
                "--set", f"57:13.width={vid_w}",
                "--set", f"57:13.height={vid_h}",
            )
            saved = _find_saved_file(output)
            if saved:
                shutil.copy2(saved, str(img_path))
                scene["image_path"] = str(img_path)
                results["images"] += 1
            else:
                results["failed"].append({"scene": scene["id"], "step": "image"})

    # Step: Split audio
    if step in (None, "audio"):
        import subprocess as sp
        seg_dir = project_dir / "segments"
        for scene in target_scenes:
            seg_path = seg_dir / f"scene_{scene['id']:03d}.wav"
            if seg_path.exists():
                scene["audio_path"] = str(seg_path)
                continue
            duration = scene["end"] - scene["start"]
            sp.run([
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(scene["start"]), "-t", str(duration),
                "-ar", "44100", "-ac", "1", str(seg_path),
            ], capture_output=True)
            scene["audio_path"] = str(seg_path)
            results["audio"] += 1

    # Step: Generate video clips
    if step in (None, "clips"):
        for scene in target_scenes:
            clip_path = project_dir / "clips" / f"scene_{scene['id']:03d}.mp4"
            if clip_path.exists():
                scene["video_path"] = str(clip_path)
                continue
            if not scene.get("image_path") or not scene.get("audio_path"):
                results["failed"].append({"scene": scene["id"], "step": "clip", "reason": "missing assets"})
                continue

            # Upload
            img_remote = _upload_file(scene["image_path"])
            audio_remote = _upload_file(scene["audio_path"])
            if not img_remote or not audio_remote:
                results["failed"].append({"scene": scene["id"], "step": "clip", "reason": "upload failed"})
                continue

            duration = scene["end"] - scene["start"]

            output = _run_comfy(
                "go", "presets/ltx23-a2v_workflow.json",
                "--set", f"269.image={img_remote}",
                "--set", f"276.audio={audio_remote}",
                "--set", f"340:319.value={scene.get('motion_prompt', 'cinematic motion')}",
                "--set", f"340:331.value={duration}",
                "--set", f"340:285.noise_seed={scene.get('seed', 42 + scene['id'])}",
                timeout=300,
            )
            saved = _find_saved_file(output)
            if saved:
                shutil.copy2(saved, str(clip_path))
                scene["video_path"] = str(clip_path)
                results["clips"] += 1
            else:
                results["failed"].append({"scene": scene["id"], "step": "clip"})

    # Save progress
    data["scenes"] = all_scenes
    sb_path.write_text(json.dumps(data, indent=2))

    return json.dumps({
        "status": "generated",
        **results,
        "total_scenes": len(target_scenes),
    }, indent=2)


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

    # Build concat file
    concat_file = project_dir / "concat.txt"
    lines = []
    valid = 0
    for scene in scenes:
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

    status = {
        "title": data.get("title", ""),
        "duration": round(data.get("duration", 0), 1),
        "total_scenes": len(scenes),
        "has_prompts": sum(1 for s in scenes if s.get("prompt")),
        "has_images": sum(1 for s in scenes if s.get("image_path") and Path(s["image_path"]).exists()),
        "has_audio": sum(1 for s in scenes if s.get("audio_path") and Path(s["audio_path"]).exists()),
        "has_clips": sum(1 for s in scenes if s.get("video_path") and Path(s["video_path"]).exists()),
        "missing_prompts": [s["id"] for s in scenes if not s.get("prompt")],
        "missing_clips": [s["id"] for s in scenes if not (s.get("video_path") and Path(s["video_path"]).exists())],
    }

    output_candidates = list(project_dir.glob("*_Music_Video.mp4"))
    if output_candidates:
        latest = max(output_candidates, key=lambda p: p.stat().st_mtime)
        status["output"] = str(latest)
        status["output_size_mb"] = round(latest.stat().st_size / (1024 * 1024), 1)

    return json.dumps(status, indent=2)


# ── Helpers for music video ─────────────────────────────────────────────


def _find_saved_file(output: str) -> str | None:
    """Extract saved file path from comfy.sh output."""
    if not output:
        return None
    for line in output.splitlines():
        if line.strip().startswith("Saved "):
            path = line.strip().replace("Saved ", "").strip()
            full = COMFY_SH.parent / path
            if full.exists():
                return str(full)
    return None


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


# ── Entry point ──────────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
