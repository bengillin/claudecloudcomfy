# claudecloudcomfy

A creative agent toolkit for [Comfy Cloud](https://docs.comfy.org/development/cloud/overview). Run ComfyUI workflows from your terminal with a battle-tested bash CLI, or let Claude autonomously plan and execute multi-step creative pipelines via MCP.

## Setup

1. Get an API key at [platform.comfy.org/profile/api-keys](https://platform.comfy.org/profile/api-keys)

2. Configure:
   ```bash
   cp .env.example .env
   # paste your API key into .env
   ```

3. Install Python dependencies (for MCP server):
   ```bash
   brew install uv  # if you don't have uv
   uv sync
   ```

4. Test:
   ```bash
   ./comfy.sh user
   ```

## Creative agent (MCP)

The MCP server turns Claude into a creative agent that can plan, generate, evaluate, and iterate autonomously. When running Claude Code in this directory, the server auto-connects.

```
User → Claude Code → MCP Server (Python) → comfy.sh → Comfy Cloud API
                ↑                                           ↓
                └──── vision evaluation ←── downloaded outputs
```

**Try it:**

```
> "Create a promo image for my app — basketball card with neon holographic effect, floating in space"
```

Claude will:
1. Read presets → pick `z-turbo`
2. Read z-turbo's prompt guide → craft an optimized prompt
3. Call `comfy_generate` → get image
4. View the image → evaluate quality
5. If good: return it. If not: retry with adjusted prompt/seed

**Multi-step projects:**

```
> "Start a marketing project. I need a hero image, 3 angle variations, and an animated version."
```

Claude will create a project, generate and evaluate each step, and log everything for continuity across sessions.

**Parallel generation:**

```
> "Give me 4 variations of that hero image with different seeds"
```

Claude will use `comfy_submit` to fire off all 4 jobs simultaneously, then collect results with `comfy_job_wait` — no blocking.

### MCP tools

| Tool | Description |
|------|-------------|
| `comfy_generate` | Generate image from preset + prompt (blocks until done) |
| `comfy_submit` | Submit job and return immediately (for parallel generation) |
| `comfy_animate` | Image → video with img2vid preset |
| `comfy_batch_seed` | Sweep seeds for variations |
| `comfy_list_presets` | Discover presets with capabilities |
| `comfy_upload_image` | Upload image to Comfy Cloud |
| `comfy_asset_search` | Search uploaded assets |
| `comfy_job_list` | List recent jobs with status filter |
| `comfy_job_status` | Check job status |
| `comfy_job_wait` | Poll job until done + download |
| `comfy_cancel_jobs` | Cancel pending jobs |
| `comfy_download` | Download output file by name |
| `comfy_run_workflow` | Run arbitrary workflow with overrides |
| `comfy_list_outputs` | List recent downloads |
| `comfy_project_create` | Start a creative project |
| `comfy_project_list` | List all projects |
| `comfy_project_log` | Log a generation step |
| `comfy_project_status` | Get full project state |

### MCP resources & prompts

- `comfy://presets` — all preset metadata (prompt guides, capabilities, output formats)
- `comfy://presets/{name}` — single preset detail
- `creative_brief` prompt — guides Claude to plan a multi-step pipeline
- `evaluate_generation` prompt — structures visual quality evaluation

## CLI quick start

```bash
# Generate an image instantly (~15s)
./comfy.sh gen --preset=z-turbo --prompt "cyberpunk portrait, neon lighting" --open

# Animate a photo into video (~30s)
./comfy.sh animate photo.jpg --preset=wan22-i2v --prompt "the scene comes to life" --open

# Edit an image with instructions (~20s)
./comfy.sh animate photo.jpg --preset=qwen-edit --prompt "Replace the background with a beach sunset" --open

# 8 camera angles from one photo (~45s)
./comfy.sh animate portrait.jpg --preset=multi-angles --open
```

All outputs auto-download to `./downloads/`. Add `--open` to view immediately.

## Built-in presets

All presets use official, tested ComfyUI Cloud workflows with 100% open models. Every preset listed here has been verified end-to-end.

| Preset | Type | Model | Time | Output | Notes |
|--------|------|-------|------|--------|-------|
| `z-turbo` | txt2img | Z-Image Turbo | ~15s | png | 1024x1024, 8 steps |
| `wan22-i2v` | img2vid | Wan 2.2 14B dual-model | ~30s | mp4 | 640x640, 4-step LoRA |
| `ltx23-i2v` | img2vid + audio | LTX 2.3 22B | ~60s | mp4 | 720p 25fps, dual-pass upscale |
| `qwen-edit` | image edit | Qwen Edit 2509 | ~20s | png | Instruction-based, 4 steps |
| `multi-angles` | 8-angle rerender | Qwen Edit + angle LoRA | ~45s | png | 8 camera angles from 1 photo |

Each preset includes a `prompt_guide` with model-specific guidance — the MCP server exposes these via `comfy://presets/{name}` so Claude reads them before crafting prompts.

## Usage

```bash
./comfy.sh <command> [args...]
```

### Generate (text to image)

```bash
./comfy.sh gen --preset=z-turbo --prompt "oil painting of a mountain" --seed=42 --open
```

### Animate (image to video)

```bash
./comfy.sh animate photo.jpg --preset=wan22-i2v --prompt "camera zooms in slowly" --open
./comfy.sh animate photo.jpg --preset=ltx23-i2v --prompt "the scene comes alive with motion"
```

### Edit images

```bash
./comfy.sh animate photo.jpg --preset=qwen-edit --prompt "Make it look like a watercolor painting" --open
./comfy.sh animate portrait.jpg --preset=multi-angles --open
```

### Run any workflow directly

```bash
./comfy.sh run workflow.json
./comfy.sh run-with workflow.json --set 6.text="a cyberpunk city" --set 3.seed=42
./comfy.sh go workflow.json --set 6.text="a cat in space" --open
```

### Batch runs

```bash
./comfy.sh batch-seed workflow.json "3" 1 10                              # Sweep seeds
./comfy.sh batch-file workflow.json "6" "text" prompts.txt                # From file
./comfy.sh batch-grid workflow.json seeds.txt prompts.txt "3" "6" "text"  # Grid
```

### Save your own presets

Export any workflow from ComfyUI Cloud as API-format JSON, then:

```bash
./comfy.sh preset-save mypreset workflow_api.json \
  --prompt-node=6.text --seed-node=3 --image-node=5 \
  --desc="My custom workflow"

./comfy.sh gen --preset=mypreset --prompt "your prompt"
./comfy.sh animate photo.jpg --preset=mypreset --prompt "animate this"
./comfy.sh preset-list
```

### Monitor and manage

```bash
./comfy.sh monitor                          # Live WebSocket progress bars
./comfy.sh jobs --status=completed          # List jobs
./comfy.sh poll <job_id> --download --open  # Wait + download
./comfy.sh cancel <job_id>                  # Cancel pending
./comfy.sh interrupt                        # Stop running
```

### Assets and files

```bash
./comfy.sh asset-upload file.png --tag=input
./comfy.sh asset-bulk-upload ./images/ --tag=input
./comfy.sh asset-search "portrait" --tag=models
./comfy.sh asset-cleanup --older-than=30d --dry-run
./comfy.sh download <filename>
```

### Browse cloud resources

```bash
./comfy.sh models                  # Model folders
./comfy.sh models-in checkpoints   # Models in folder
./comfy.sh nodes                   # All available nodes
./comfy.sh help                    # Full command list
```

## What's in the box

| File/Dir | Description |
|----------|-------------|
| `comfy.sh` | CLI — every API endpoint + gen, animate, batch, presets, monitoring |
| `mcp_server/server.py` | FastMCP server — 18 tools, 2 resources, 2 prompts |
| `mcp_server/config.py` | Path resolution + .env loading for MCP server |
| `.mcp.json` | Claude Code auto-connection config |
| `tests/test_server.py` | 29 tests — presets, tools, errors, projects (no API calls) |
| `.github/workflows/` | CI — runs tests on push/PR via GitHub Actions |
| `pyproject.toml` | Python project config (uv, mcp dependency) |
| `workflows/` | 5 official ComfyUI Cloud workflow JSONs (all verified) |
| `presets/` | Preset configs with prompt guides and capabilities |
| `projects/` | Creative project tracking (created by MCP tools) |
| `downloads/` | Generated outputs land here |
| `REFERENCE.md` | Full API endpoint reference |
| `openapi-cloud.yaml` | Official OpenAPI 3.0.3 spec (3,700+ lines) |
| `.env.example` | API key template |

## Requirements

- bash, curl, python3
- [uv](https://docs.astral.sh/uv/) (for MCP server Python dependencies)
- A [Comfy Cloud](https://www.comfy.org/cloud/pricing) subscription
- Optional: [wscat](https://github.com/websockets/wscat) or [websocat](https://github.com/vi/websocat) for WebSocket monitoring

## API overview

**Base URL:** `https://cloud.comfy.org`
**Auth:** `X-API-Key` header
**WebSocket:** `wss://cloud.comfy.org/ws?clientId={uuid}&token={api_key}`

See [REFERENCE.md](REFERENCE.md) for the full endpoint map or the [official docs](https://docs.comfy.org/development/cloud/overview).

## Shoutout

Big shoutout to the great and powerful [@PurzBeats](https://x.com/PurzBeats) for the inspiration and guidance on this project.

## License

MIT
