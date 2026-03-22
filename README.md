# claudecloudcomfy

A command-line toolkit for the [Comfy Cloud API](https://docs.comfy.org/development/cloud/overview). Run ComfyUI workflows on cloud infrastructure from your terminal — with built-in txt2vid presets, batch generation, and auto-download.

## Setup

1. Get an API key at [platform.comfy.org/profile/api-keys](https://platform.comfy.org/profile/api-keys)

2. Configure:
   ```bash
   cp .env.example .env
   # paste your API key into .env
   ```

3. Test:
   ```bash
   ./comfy.sh user
   ```

## Quick start: Generate a video

```bash
# Fast draft (Wan 2.1 1.3B — ~80 seconds)
./comfy.sh gen --preset=wan-fast --prompt "a wolf running through deep snow in a forest"

# Best open quality (Wan 2.2 14B)
./comfy.sh gen --preset=wan-14b --prompt "a wolf running through deep snow in a forest"

# 720p cinematic (SkyReels V2 14B)
./comfy.sh gen --preset=skyreels --prompt "a samurai on a cliff in a thunderstorm"
```

Videos auto-download to `./downloads/` and can auto-open with `--open`.

## Built-in txt2vid presets

| Preset | Model | Resolution | Speed | Best for |
|--------|-------|-----------|-------|----------|
| `wan-fast` | Wan 2.1 1.3B | 832x480 | Fast | Quick drafts, iteration |
| `wan-14b` | Wan 2.2 14B | 832x480 | Slow | Best open-source quality |
| `skyreels` | SkyReels V2 14B | 1280x720 | Slow | 720p cinematic |
| `cogvideo` | CogVideoX 5B | 720x480 | Medium | Strong motion coherence |
| `hunyuan` | HunyuanVideo 1.5 | 848x480 | Medium | Smooth motion |
| `ltx` | LTX Video 2 19B | 768x512 | Fast | Fast high-res |
| `mochi` | Mochi Preview | 848x480 | Medium | Different aesthetic |

All presets are 100% open models running on cloud GPU — no per-generation API fees beyond your Comfy Cloud subscription.

## Usage

```bash
./comfy.sh <command> [args...]
```

### Generate with presets

```bash
# Generate with any preset
./comfy.sh gen --preset=wan-fast --prompt "neon Tokyo street at night" --open

# Set a specific seed
./comfy.sh gen --preset=cogvideo --prompt "a flower blooming" --seed=42

# List available presets
./comfy.sh preset-list
```

### Save your own presets

```bash
# Save a workflow as a reusable preset
./comfy.sh preset-save mypreset workflow_api.json --prompt-node=6.text --seed-node=3 --desc="My custom workflow"

# Use it
./comfy.sh gen --preset=mypreset --prompt "your prompt here"

# Manage presets
./comfy.sh preset-show mypreset
./comfy.sh preset-delete mypreset
```

### Run workflows directly

```bash
# Submit a workflow (save as "API Format" JSON from ComfyUI)
./comfy.sh run workflow.json

# Override multiple inputs at once
./comfy.sh run-with workflow.json --set 6.text="a cyberpunk city" --set 3.seed=42

# Legacy single override (still works)
./comfy.sh run-with workflow.json "3" "seed" "42"

# Submit → poll → auto-download in one shot
./comfy.sh go workflow.json --set 6.text="a cat in space" --open
```

### Batch runs

```bash
# Sweep seeds 1-10
./comfy.sh batch-seed workflow.json "3" 1 10

# One job per line in a text file
./comfy.sh batch-file workflow.json "6" "text" prompts.txt

# Cartesian product: every seed x every prompt
./comfy.sh batch-grid workflow.json seeds.txt prompts.txt "3" "6" "text"
```

### Partner nodes (Flux Pro, Ideogram, etc.)

```bash
# Pass partner key per-command
./comfy.sh go workflow.json --partner-key=your_key

# Or set it in .env for all commands
# COMFY_PARTNER_KEY=your_key
```

### Monitor in real-time

```bash
# Pretty-printed WebSocket stream with progress bars
./comfy.sh monitor

# Filter to a specific job
./comfy.sh monitor --job=<prompt_id>

# Raw WebSocket (for debugging)
./comfy.sh ws
```

### Manage jobs

```bash
./comfy.sh jobs                        # List all jobs
./comfy.sh jobs --status=completed     # Filter by status
./comfy.sh job <job_id>                # Full job detail
./comfy.sh poll <job_id> --download    # Poll until done + download
./comfy.sh queue                       # View queue
./comfy.sh cancel <job_id>             # Cancel pending job
./comfy.sh interrupt                   # Stop running jobs
```

### Download outputs

```bash
./comfy.sh job <job_id>                # See output filenames
./comfy.sh download ComfyUI_00001_.png # Saves to ./downloads/
```

### Asset management

```bash
# Single operations
./comfy.sh asset-upload model.safetensors --tag=models
./comfy.sh asset-upload-url <url> <name> --tag=models
./comfy.sh asset-download-bg <huggingface_url>

# Bulk operations
./comfy.sh asset-bulk-upload ./my_images/ --tag=input
./comfy.sh asset-bulk-tag lora id1 id2 id3
./comfy.sh asset-bulk-delete id1 id2 --yes
./comfy.sh asset-search "realistic" --tag=models
./comfy.sh asset-cleanup --older-than=30d --tag=temp --dry-run
```

### Browse models & nodes

```bash
./comfy.sh models                  # List model folders
./comfy.sh models-in checkpoints   # Models in a folder
./comfy.sh nodes                   # All available nodes
```

### Full command list

```bash
./comfy.sh help
```

## What's in the box

| File/Dir | Description |
|----------|-------------|
| `comfy.sh` | CLI wrapper — every API endpoint + batch, presets, monitoring |
| `workflows/` | Ready-to-run txt2vid workflow JSONs (Wan, CogVideo, Hunyuan, LTX, Mochi) |
| `REFERENCE.md` | Quick-reference for all endpoints, statuses, and WebSocket messages |
| `openapi-cloud.yaml` | Official OpenAPI 3.0.3 spec (3,700+ lines) |
| `.env.example` | API key template (supports partner keys) |
| `presets/` | Saved workflow presets (created locally via `preset-save`) |
| `downloads/` | Generated outputs land here |

## Requirements

- bash, curl, python3
- A [Comfy Cloud](https://www.comfy.org/cloud/pricing) subscription
- Optional: [wscat](https://github.com/websockets/wscat) or [websocat](https://github.com/vi/websocat) for WebSocket streaming/monitoring

## API overview

**Base URL:** `https://cloud.comfy.org`
**Auth:** `X-API-Key` header
**WebSocket:** `wss://cloud.comfy.org/ws?clientId={uuid}&token={api_key}`

See [REFERENCE.md](REFERENCE.md) for the full endpoint map or the [official docs](https://docs.comfy.org/development/cloud/overview).

## Shoutout

Big shoutout to the great and powerful [@PurzBeats](https://x.com/PurzBeats) for the inspiration and guidance on this project.

## License

MIT
