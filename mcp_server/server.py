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


# ── Entry point ──────────────────────────────────────────────────────────


def main():
    mcp.run()


if __name__ == "__main__":
    main()
