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

# Track running MV generations
_running: set[str] = set()


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
    suffix = Path(file.filename or "upload").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(DOWNLOADS_DIR)) as tmp:
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
async def api_mv_plan(project: str, file: UploadFile = File(...), title: str = Form("")):
    suffix = Path(file.filename or "audio.mp3").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(DOWNLOADS_DIR)) as tmp:
        tmp.write(await file.read())
        audio_path = tmp.name
    return _j(await _bg(comfy_mv_plan, audio_path=audio_path, title=title or project, project_name=project))


@app.post("/api/mv/{project}/elements")
async def api_mv_add_element(project: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_add_element, project_name=project, **body))


@app.get("/api/mv/{project}/elements")
async def api_mv_elements(project: str):
    return _j(await _bg(comfy_mv_list_elements, project_name=project))


@app.put("/api/mv/{project}/elements/{element_id}")
async def api_mv_update_element(project: str, element_id: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_update_element, project_name=project, element_id=element_id, **body))


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
    if project in _running:
        return {"status": "already_running"}
    _running.add(project)

    async def run():
        try:
            await _bg(comfy_mv_generate, project_name=project, **body)
        finally:
            _running.discard(project)

    asyncio.create_task(run())
    return {"status": "started"}


@app.post("/api/mv/{project}/stitch")
async def api_mv_stitch(project: str, req: Request):
    body = await req.json()
    return _j(await _bg(comfy_mv_stitch, project_name=project, **body))


@app.get("/api/mv/{project}/status")
async def api_mv_status_route(project: str):
    result = _j(await _bg(comfy_mv_status, project_name=project))
    result["generating"] = project in _running
    return result
