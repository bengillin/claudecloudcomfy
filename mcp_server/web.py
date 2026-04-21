"""FastAPI HTTP layer — thin wrapper over existing MCP tool functions."""

import asyncio
import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import PROJECT_ROOT, DOWNLOADS_DIR, PROJECTS_DIR, PRESETS_DIR
from .server import (
    comfy_list_presets,
    comfy_generate,
    comfy_submit,
    comfy_animate,
    comfy_batch_seed,
    comfy_upload_image,
    comfy_asset_search,
    comfy_job_list,
    comfy_job_status,
    comfy_job_wait,
    comfy_cancel_jobs,
    comfy_download,
    comfy_run_workflow,
    comfy_list_outputs,
    comfy_project_create,
    comfy_project_list,
    comfy_project_log,
    comfy_project_status,
    comfy_mv_plan,
    comfy_mv_set_brief,
    comfy_mv_get_brief,
    comfy_mv_add_element,
    comfy_mv_generate_element,
    comfy_mv_list_elements,
    comfy_mv_update_element,
    comfy_mv_set_prompts,
    comfy_mv_generate,
    comfy_mv_stitch,
    comfy_mv_status,
    _load_preset,
    _list_preset_names,
)

app = FastAPI(title="Comfy Cloud Studio")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Per-project state: running flag + last comfy_mv_generate result (warnings, failed, counts).
# Persists across the fire-and-forget /api/mv/{project}/generate boundary so the UI can
# pull it via /status instead of missing the response.
_mv_state: dict[str, dict] = {}


# ── Helpers ──────────────────────────────────────────────────────────────


def _j(result: str) -> dict:
    """Parse JSON string from MCP tool into dict."""
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return {"raw": result}


async def _bg(fn, *args, **kwargs):
    """Run blocking MCP tool function in thread pool."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _safe_path(requested: str, *allowed_roots: Path) -> Path | None:
    """Resolve path and verify it's within allowed directories."""
    p = Path(requested).resolve()
    for root in allowed_roots:
        if p.is_relative_to(root.resolve()):
            return p
    return None


# ── Static ───────────────────────────────────────────────────────────────


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/files/{filepath:path}")
async def serve_file(filepath: str):
    p = _safe_path(filepath, PROJECT_ROOT)
    if not p or not p.exists() or not p.is_file():
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(p)


# ── Presets ──────────────────────────────────────────────────────────────


@app.get("/api/health")
async def api_health():
    """Check if API key is configured and comfy.sh is working."""
    import os
    has_key = bool(os.environ.get("COMFY_API_KEY"))
    if not has_key:
        # Check .env file
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.strip().startswith("COMFY_API_KEY") and "your_api_key_here" not in line:
                    has_key = True
                    break
    return {"ok": has_key, "presets_dir": str(PRESETS_DIR), "has_presets": PRESETS_DIR.exists() and any(PRESETS_DIR.glob("*.json"))}


@app.get("/api/presets")
async def api_presets():
    return _j(await _bg(comfy_list_presets))


@app.get("/api/presets/{name}")
async def api_preset_detail(name: str):
    try:
        return _load_preset(name)
    except FileNotFoundError:
        return JSONResponse({"error": f"Preset '{name}' not found"}, 404)


# ── Generation ───────────────────────────────────────────────────────────


@app.post("/api/generate")
async def api_generate(req: Request):
    body = await req.json()
    return _j(await _bg(comfy_generate, **body))


@app.post("/api/submit")
async def api_submit(req: Request):
    body = await req.json()
    return _j(await _bg(comfy_submit, **body))


@app.post("/api/animate")
async def api_animate(req: Request):
    body = await req.json()
    return _j(await _bg(comfy_animate, **body))


# ── Jobs ─────────────────────────────────────────────────────────────────


@app.get("/api/jobs")
async def api_jobs(status: str | None = None, limit: int | None = None):
    result = await _bg(comfy_job_list, status=status, limit=limit)
    return _j(result)


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    return _j(await _bg(comfy_job_status, job_id=job_id))


@app.post("/api/jobs/{job_id}/wait")
async def api_job_wait(job_id: str):
    return _j(await _bg(comfy_job_wait, job_id=job_id))


@app.post("/api/jobs/cancel")
async def api_cancel_jobs(req: Request):
    body = await req.json()
    return _j(await _bg(comfy_cancel_jobs, job_ids=body.get("job_ids", [])))


# ── Upload ───────────────────────────────────────────────────────────────


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    # Use system temp so we don't pollute the user-facing downloads gallery.
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    return _j(await _bg(comfy_upload_image, local_path=tmp_path))


# ── Outputs ──────────────────────────────────────────────────────────────


@app.get("/api/outputs")
async def api_outputs(limit: int = 50, extension: str | None = None):
    return _j(await _bg(comfy_list_outputs, limit=limit, extension=extension))


@app.get("/api/outputs/{filename}")
async def api_output_file(filename: str):
    p = DOWNLOADS_DIR / filename
    if not p.exists():
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(p)


# ── Music Video ──────────────────────────────────────────────────────────


@app.post("/api/mv/{project}/plan")
async def api_mv_plan(
    project: str,
    file: UploadFile = File(...),
    title: str = Form(""),
    width: int = Form(1280),
    height: int = Form(720),
    lyrics: str = Form(""),
):
    suffix = Path(file.filename or "audio.mp3").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        audio_path = tmp.name
    return _j(await _bg(
        comfy_mv_plan,
        audio_path=audio_path,
        title=title or project,
        project_name=project,
        width=width,
        height=height,
        lyrics=lyrics or None,
    ))


@app.get("/api/mv/{project}/brief")
async def api_mv_get_brief(project: str):
    return _j(await _bg(comfy_mv_get_brief, project_name=project))


@app.post("/api/mv/{project}/brief")
async def api_mv_set_brief(project: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_set_brief, project_name=project, **body))


@app.post("/api/mv/{project}/elements")
async def api_mv_add_element(
    project: str,
    element_id: str = Form(...),
    category: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    # Save uploaded files to system temp; comfy_mv_add_element copies them into the project.
    source_paths = []
    for f in files:
        if f.filename:
            suffix = Path(f.filename).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(await f.read())
                source_paths.append(tmp.name)

    return _j(await _bg(
        comfy_mv_add_element,
        project_name=project,
        element_id=element_id,
        category=category,
        name=name,
        description=description,
        source_images=source_paths or None,
    ))


@app.get("/api/mv/{project}/elements")
async def api_mv_elements(project: str):
    return _j(await _bg(comfy_mv_list_elements, project_name=project))


@app.put("/api/mv/{project}/elements/{element_id}")
async def api_mv_update_element(project: str, element_id: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_update_element, project_name=project, element_id=element_id, **body))


@app.post("/api/mv/{project}/elements/{element_id}/upload")
async def api_mv_upload_source(project: str, element_id: str, files: list[UploadFile] = File(...)):
    """Upload source images to an existing element."""
    from .music_video import Storyboard

    project_dir = PROJECTS_DIR / project
    sb_path = project_dir / "storyboard.json"
    if not sb_path.exists():
        return JSONResponse({"error": "project not found"}, 404)

    sb = Storyboard.load(sb_path)
    element = sb.get_element(element_id)
    if not element:
        return JSONResponse({"error": "element not found"}, 404)

    elements_dir = project_dir / "elements" / element_id
    elements_dir.mkdir(parents=True, exist_ok=True)

    added = []
    for f in files:
        if f.filename:
            suffix = Path(f.filename).suffix
            dest = elements_dir / f"source_{f.filename}"
            dest.write_bytes(await f.read())
            element.source_images.append(str(dest))
            added.append(str(dest))

    sb.save(sb_path)
    return {"status": "uploaded", "added": len(added), "total_sources": len(element.source_images)}


@app.post("/api/mv/{project}/elements/{element_id}/generate")
async def api_mv_generate_element(project: str, element_id: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_generate_element, project_name=project, element_id=element_id, **body))


@app.post("/api/mv/{project}/prompts")
async def api_mv_set_prompts(project: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_set_prompts, project_name=project, **body))


@app.post("/api/mv/{project}/generate")
async def api_mv_generate_start(project: str, req: Request):
    body = await req.json()
    state = _mv_state.setdefault(project, {"running": False, "last_result": None})
    if state["running"]:
        return {"status": "already_running"}
    state["running"] = True

    async def run():
        try:
            result = await _bg(comfy_mv_generate, project_name=project, **body)
            state["last_result"] = _j(result)
        except Exception as e:
            state["last_result"] = {"error": str(e), "failed": [], "warnings": [
                {"type": "generate_exception", "message": str(e)}
            ]}
        finally:
            state["running"] = False

    asyncio.create_task(run())
    return {"status": "started"}


@app.post("/api/mv/{project}/stitch")
async def api_mv_stitch(project: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_stitch, project_name=project, **body))


@app.get("/api/mv/{project}/status")
async def api_mv_status_route(project: str):
    result = _j(await _bg(comfy_mv_status, project_name=project))
    state = _mv_state.get(project) or {}
    result["generating"] = bool(state.get("running"))
    last = state.get("last_result") or {}
    if last.get("warnings"):
        result["last_warnings"] = last["warnings"]
    if last.get("failed"):
        result["last_failed"] = last["failed"]

    # Per-scene asset paths so the UI can render thumbnails as they appear.
    sb_path = PROJECTS_DIR / project / "storyboard.json"
    if sb_path.exists():
        try:
            data = json.loads(sb_path.read_text())
            result["scene_assets"] = {
                s["id"]: {
                    "image_path": s.get("image_path") or "",
                    "video_path": s.get("video_path") or "",
                }
                for s in data.get("scenes") or []
            }
        except Exception:
            pass
    return result


@app.get("/api/mv/projects")
async def api_mv_projects():
    """List existing music-video projects with basic metadata, newest first."""
    out = []
    if PROJECTS_DIR.exists():
        for p in sorted(PROJECTS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_dir():
                continue
            sb = p / "storyboard.json"
            if not sb.exists():
                continue
            try:
                data = json.loads(sb.read_text())
            except Exception:
                continue
            scenes = data.get("scenes") or []
            has_clips = sum(1 for s in scenes if s.get("video_path"))
            final = p / f"{(data.get('title') or p.name).replace(' ', '_')}_Music_Video.mp4"
            out.append({
                "name": p.name,
                "title": data.get("title", p.name),
                "duration": round(data.get("duration", 0), 1),
                "scene_count": len(scenes),
                "clip_count": has_clips,
                "has_final": final.exists(),
                "final_path": str(final) if final.exists() else None,
                "mtime": p.stat().st_mtime,
            })
    return {"projects": out}


@app.delete("/api/mv/{project}")
async def api_mv_delete(project: str):
    """Delete a music-video project directory (storyboard + scenes + clips + segments + elements)."""
    import shutil
    proj = PROJECTS_DIR / project
    # Guard: must be under PROJECTS_DIR, must look like a real project.
    try:
        proj_resolved = proj.resolve()
        if not proj_resolved.is_relative_to(PROJECTS_DIR.resolve()):
            return JSONResponse({"error": "invalid project path"}, 400)
    except Exception:
        return JSONResponse({"error": "invalid project path"}, 400)
    if not proj.exists() or not (proj / "storyboard.json").exists():
        return JSONResponse({"error": "project not found"}, 404)
    shutil.rmtree(proj)
    _mv_state.pop(project, None)
    return {"status": "deleted", "project": project}


@app.get("/api/mv/{project}/load")
async def api_mv_load(project: str):
    """Full storyboard snapshot so the UI can resume a project (brief + scenes + aspect ratio)."""
    sb_path = PROJECTS_DIR / project / "storyboard.json"
    if not sb_path.exists():
        return JSONResponse({"error": "project not found"}, 404)
    try:
        data = json.loads(sb_path.read_text())
    except Exception as e:
        return JSONResponse({"error": f"corrupted storyboard: {e}"}, 500)
    final_name = f"{(data.get('title') or project).replace(' ', '_')}_Music_Video.mp4"
    final = PROJECTS_DIR / project / final_name
    return {
        "title": data.get("title"),
        "duration": data.get("duration"),
        "width": data.get("width", 1280),
        "height": data.get("height", 720),
        "camera_style": data.get("camera_style", ""),
        "global_style": data.get("global_style", ""),
        "brief": data.get("brief") or {},
        "scenes": data.get("scenes") or [],
        "elements": data.get("elements") or [],
        "final_video": str(final) if final.exists() else None,
    }


@app.delete("/api/mv/{project}/elements/{element_id}/references")
async def api_mv_remove_reference(project: str, element_id: str, req: Request):
    """Remove a single reference image path from an element (used by the ref gallery)."""
    body = await req.json()
    path = body.get("path")
    if not path:
        return JSONResponse({"error": "missing path"}, 400)
    return _j(await _bg(
        comfy_mv_update_element,
        project_name=project,
        element_id=element_id,
        remove_reference=path,
    ))
