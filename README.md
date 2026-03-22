# claudecloudcomfy

A command-line toolkit for the [Comfy Cloud API](https://docs.comfy.org/development/cloud/overview). Run ComfyUI workflows on cloud infrastructure from your terminal — 5 verified presets built from official ComfyUI Cloud workflows.

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

## Quick start

```bash
# Generate an image instantly (~7s)
./comfy.sh gen --preset=z-turbo --prompt "cyberpunk portrait, neon lighting" --open

# Animate a photo into video (~30s)
./comfy.sh animate photo.jpg --preset=wan22-i2v --prompt "the scene comes to life" --open

# Edit an image with instructions (~10s)
./comfy.sh animate photo.jpg --preset=qwen-edit --prompt "Replace the background with a beach sunset" --open

# 8 camera angles from one photo (~65s)
./comfy.sh animate portrait.jpg --preset=multi-angles --open
```

All outputs auto-download to `./downloads/`. Add `--open` to view immediately.

## Built-in presets

All presets use official, tested ComfyUI Cloud workflows with 100% open models. Every preset listed here has been verified end-to-end.

| Preset | Type | Model | Time | Notes |
|--------|------|-------|------|-------|
| `z-turbo` | txt2img | Z-Image Turbo | ~7s | 1024x1024, 8 steps |
| `wan22-i2v` | img2vid | Wan 2.2 14B dual-model | ~30s | 640x640, 4-step LoRA |
| `ltx23-i2v` | img2vid + audio | LTX 2.3 22B | ~35s | 1280x720, dual-pass upscale |
| `qwen-edit` | image edit | Qwen Edit 2509 | ~10s | Instruction-based, 4 steps |
| `multi-angles` | 8-angle rerender | Qwen Edit + angle LoRA | ~65s | 8 camera angles from 1 photo |

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
| `workflows/` | 5 official ComfyUI Cloud workflow JSONs (all verified) |
| `presets/` | Saved preset configs (created locally via `preset-save`) |
| `downloads/` | Generated outputs land here |
| `REFERENCE.md` | Full API endpoint reference |
| `openapi-cloud.yaml` | Official OpenAPI 3.0.3 spec (3,700+ lines) |
| `.env.example` | API key template |

## Requirements

- bash, curl, python3
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
