# claudecloudcomfy

A command-line toolkit for the [Comfy Cloud API](https://docs.comfy.org/development/cloud/overview). Run ComfyUI workflows on cloud infrastructure from your terminal.

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

## Usage

```bash
./comfy.sh <command> [args...]
```

### Run workflows

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

### Presets

```bash
# Save a workflow as a reusable preset
./comfy.sh preset-save sdxl workflow_api.json --prompt-node=6.text --seed-node=3 --desc="SDXL txt2img"

# Generate from a preset
./comfy.sh gen --preset=sdxl --prompt "a mountain at sunset" --seed=42 --open

# Random seed by default
./comfy.sh gen --preset=sdxl --prompt "neon Tokyo street"

# List and manage presets
./comfy.sh preset-list
./comfy.sh preset-show sdxl
./comfy.sh preset-delete sdxl
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

| File | Description |
|------|-------------|
| `comfy.sh` | CLI wrapper — every endpoint + batch, presets, monitoring |
| `REFERENCE.md` | Quick-reference for all endpoints, statuses, and WebSocket messages |
| `openapi-cloud.yaml` | Official OpenAPI 3.0.3 spec (3,700+ lines) |
| `.env.example` | API key template (supports partner keys) |
| `presets/` | Saved workflow presets (created via `preset-save`) |

## Requirements

- bash, curl, python3 (for JSON formatting and URL encoding)
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
