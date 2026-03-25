"""Path resolution and environment config for the Comfy MCP server."""

import os
from pathlib import Path

# Project root = parent of mcp_server/
PROJECT_ROOT = Path(__file__).resolve().parent.parent

COMFY_SH = PROJECT_ROOT / "comfy.sh"
PRESETS_DIR = PROJECT_ROOT / "presets"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
PROJECTS_DIR = PROJECT_ROOT / "projects"

# Load .env if present (comfy.sh also does this, but we need COMFY_API_KEY
# available in the environment for subprocess calls)
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value
