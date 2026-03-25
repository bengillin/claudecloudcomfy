# claudecloudcomfy — Comfy Cloud Creative Agent

## What this is
A bash CLI (`comfy.sh`) wrapping the Comfy Cloud API, with an MCP server that turns Claude into a creative agent.

## Architecture
- `comfy.sh` — battle-tested execution engine (bash CLI, don't modify unless necessary)
- `mcp_server/server.py` — FastMCP server wrapping comfy.sh with 23 tools
- `presets/*.json` — preset configs with prompt guides and capabilities
- `projects/` — persistent creative project tracking

## Creative Agent Workflow
When given a creative brief:
1. Call `comfy_list_presets` to discover available presets
2. Read `comfy://presets/{name}` resource for the prompt guide
3. Craft prompts following the guide, then call `comfy_generate` or `comfy_animate`
4. View the output file (you have vision) and evaluate quality
5. Iterate: adjust prompt, try different seed, or switch preset

### Parallel Generation
For exploring variations or multi-step pipelines, use `comfy_submit` to fire off
multiple jobs without blocking, then collect results with `comfy_job_wait`:
1. `comfy_submit(preset, prompt, seed=N)` → returns job_id immediately
2. Submit as many as needed (different seeds, prompts, or presets)
3. `comfy_job_wait(job_id)` for each → downloads output when ready
4. Compare outputs visually and pick the best

## Key Commands (comfy.sh)
- `gen --preset=X --prompt "..." --seed N` — generate from preset
- `animate <img> --preset=X --prompt "..."` — image to video
- `batch-seed <wf> <seed_node> <start> <end>` — seed sweep
- `go <wf.json> --set node.field=value` — run arbitrary workflow

## Presets
- `z-turbo` — txt2img, fast, 1024x1024
- `wan22-i2v` — img2vid, high quality
- `ltx23-i2v` — img2vid with audio
- `qwen-edit` — instruction-based image editing
- `multi-angles` — 8-angle rerender from single image

## Music Video Pipeline
When given a song file:
1. Call `comfy_mv_plan` → transcribe + build storyboard with timed scenes
2. Review scenes, then call `comfy_mv_set_prompts` with visual + motion prompts per scene
3. Call `comfy_mv_generate` → generates images (z-turbo 1280x720), splits audio, runs LTX 2.3 a2v
4. Call `comfy_mv_status` to check progress and find failures
5. Retry failed scenes: `comfy_mv_generate(scenes=[29])` to regenerate specific ones
6. Call `comfy_mv_stitch` → concatenates clips + overlays original audio track
7. View output, refine prompts, regenerate scenes as needed

Preset `ltx23-a2v` is the audio-conditioned variant: image + audio segment → video synced to music.

## Development
- Python managed with `uv` (pyproject.toml)
- MCP server: `uv run python -m mcp_server.server`
- Test import: `uv run python -c "from mcp_server.server import mcp"`
