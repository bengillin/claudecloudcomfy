# claudecloudcomfy

A command-line toolkit for the [Comfy Cloud API](https://docs.comfy.org/development/cloud/overview). Run ComfyUI workflows on cloud infrastructure from your terminal — 20 built-in presets across video, image, and editing pipelines.

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
# Generate a video from text (~80 seconds)
./comfy.sh gen --preset=wan-fast --prompt "a wolf running through deep snow in a forest"

# Animate a photo into video
./comfy.sh animate photo.jpg --preset=wan22-i2v --prompt "the scene comes to life"

# Generate an image instantly (8 steps)
./comfy.sh gen --preset=z-turbo --prompt "cyberpunk portrait, neon lighting"

# Edit an image with instructions
./comfy.sh animate photo.jpg --preset=qwen-edit --prompt "Replace the background with a beach sunset"

# One photo → multi-angle cinematic video sequence
./comfy.sh animate portrait.jpg --preset=multi-shot
```

All outputs auto-download to `./downloads/`. Add `--open` to view immediately.

## Built-in presets (20)

### Text to Video

| Preset | Model | Resolution | Speed | Best for |
|--------|-------|-----------|-------|----------|
| `wan-fast` | Wan 2.1 1.3B | 832x480 | Fast | Quick drafts, iteration |
| `wan-14b` | Wan 2.2 14B | 832x480 | Slow | Best open-source quality |
| `skyreels` | SkyReels V2 14B | 1280x720 | Slow | 720p cinematic |
| `cogvideo` | CogVideoX 5B | 720x480 | Medium | Strong motion coherence |
| `hunyuan` | HunyuanVideo 1.5 | 848x480 | Medium | Smooth motion |
| `ltx` | LTX Video 2 19B | 768x512 | Fast | Fast high-res |
| `mochi` | Mochi Preview | 848x480 | Medium | Different aesthetic |

### Image to Video

| Preset | Model | Resolution | Notes |
|--------|-------|-----------|-------|
| `wan22-i2v` | Wan 2.2 14B dual-model | 640x640 | Official, 4-step LoRA option |
| `wan-i2v` | Wan 2.2 14B | 832x480 | Standard I2V |
| `cogvideo-i2v` | CogVideoX 5B I2V | 720x480 | CogVideo animation |
| `ltx23-i2v` | LTX 2.3 22B | 1280x720 | Official, dual-pass upscale + audio |
| `ltx23-flf2v` | LTX 2.3 22B | 1280x720 | First/last frame → video + audio |

### Image Generation

| Preset | Model | Resolution | Notes |
|--------|-------|-----------|-------|
| `z-turbo` | Z-Image Turbo | 1024x1024 | 8 steps, near-instant |

### Image Editing

| Preset | Model | Notes |
|--------|-------|-------|
| `qwen-edit` | Qwen Edit 2509 | Instruction-based editing, 4 steps |
| `multi-angles` | Qwen Edit + angle LoRA | 8 camera angles from 1 photo |

### Multi-Stage Pipelines

| Preset | Pipeline | Notes |
|--------|----------|-------|
| `multi-shot` | Qwen angles → Wan 2.2 I2V → RIFE → stitch | One photo → 5-clip cinematic sequence |

All presets use 100% open models on cloud GPU.

## Usage

```bash
./comfy.sh <command> [args...]
```

### Generate (text to video/image)

```bash
./comfy.sh gen --preset=wan-fast --prompt "neon Tokyo street at night" --open
./comfy.sh gen --preset=z-turbo --prompt "oil painting of a mountain" --seed=42
./comfy.sh preset-list
```

### Animate (image to video / image editing)

```bash
./comfy.sh animate photo.jpg --preset=wan22-i2v --prompt "camera zooms in slowly" --open
./comfy.sh animate photo.jpg --preset=ltx23-i2v --prompt "the scene comes alive"
./comfy.sh animate photo.jpg --preset=qwen-edit --prompt "Make it look like a watercolor painting"
./comfy.sh animate portrait.jpg --preset=multi-shot
```

### Run workflows directly

```bash
./comfy.sh run workflow.json
./comfy.sh run-with workflow.json --set 6.text="a cyberpunk city" --set 3.seed=42
./comfy.sh go workflow.json --set 6.text="a cat in space" --open
```

### Batch runs

```bash
./comfy.sh batch-seed workflow.json "3" 1 10          # Sweep seeds
./comfy.sh batch-file workflow.json "6" "text" prompts.txt  # From file
./comfy.sh batch-grid workflow.json seeds.txt prompts.txt "3" "6" "text"  # Grid
```

### Save your own presets

```bash
./comfy.sh preset-save mypreset workflow.json \
  --prompt-node=6.text --seed-node=3 --image-node=5 \
  --desc="My custom workflow"

./comfy.sh gen --preset=mypreset --prompt "your prompt"
./comfy.sh animate photo.jpg --preset=mypreset --prompt "animate this"
```

### Partner nodes (Flux Pro, Ideogram, etc.)

```bash
./comfy.sh go workflow.json --partner-key=your_key
# Or set COMFY_PARTNER_KEY in .env
```

### Monitor and manage

```bash
./comfy.sh monitor                          # Live WebSocket progress bars
./comfy.sh monitor --job=<id>               # Filter to one job
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
| `workflows/` | 13 ready-to-run workflow JSONs (official + custom) |
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
