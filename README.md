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

# Override an input on the fly (node_id, field, value)
./comfy.sh run-with workflow.json "3" "seed" "42"

# Poll status
./comfy.sh status <job_id>

# Stream real-time progress (requires wscat or websocat)
./comfy.sh ws
```

### Manage jobs

```bash
./comfy.sh jobs                        # List all jobs
./comfy.sh jobs --status=completed     # Filter by status
./comfy.sh job <job_id>                # Full job detail
./comfy.sh queue                       # View queue
./comfy.sh cancel <job_id>             # Cancel pending job
./comfy.sh interrupt                   # Stop running jobs
```

### Download outputs

```bash
./comfy.sh job <job_id>                # See output filenames
./comfy.sh download ComfyUI_00001_.png # Saves to ./downloads/
```

### Upload inputs & assets

```bash
./comfy.sh upload photo.png                          # Legacy upload
./comfy.sh asset-upload model.safetensors --tag=models  # Asset upload
./comfy.sh asset-upload-url <url> <name> --tag=models   # Upload from URL
./comfy.sh asset-download-bg <huggingface_url>          # Background download
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
| `comfy.sh` | CLI wrapper covering every Comfy Cloud API endpoint |
| `REFERENCE.md` | Quick-reference for all endpoints, statuses, and WebSocket messages |
| `openapi-cloud.yaml` | Official OpenAPI 3.0.3 spec (3,700+ lines) |
| `.env.example` | API key template |

## Requirements

- bash, curl, python3 (for JSON formatting and URL encoding)
- Optional: [wscat](https://github.com/websockets/wscat) or [websocat](https://github.com/vi/websocat) for WebSocket streaming

## API overview

**Base URL:** `https://cloud.comfy.org`
**Auth:** `X-API-Key` header
**WebSocket:** `wss://cloud.comfy.org/ws?clientId={uuid}&token={api_key}`

See [REFERENCE.md](REFERENCE.md) for the full endpoint map or the [official docs](https://docs.comfy.org/development/cloud/overview).

## Shoutout

Big shoutout to the great and powerful [@PurzBeats](https://x.com/PurzBeats) for the inspiration and guidance on this project.

## License

MIT
