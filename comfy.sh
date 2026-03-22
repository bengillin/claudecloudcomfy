#!/usr/bin/env bash
# comfy.sh — CLI wrapper for the Comfy Cloud API
# Usage: ./comfy.sh <command> [args...]
#
# Set COMFY_API_KEY env var or create .env file with COMFY_API_KEY=your_key

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_URL="https://cloud.comfy.org"

# Load API key from .env if not set
if [[ -z "${COMFY_API_KEY:-}" ]] && [[ -f "$SCRIPT_DIR/.env" ]]; then
  export $(grep -E '^COMFY_API_KEY=' "$SCRIPT_DIR/.env" | xargs)
fi

if [[ -z "${COMFY_API_KEY:-}" ]]; then
  echo "Error: COMFY_API_KEY not set. Export it or add to $SCRIPT_DIR/.env"
  exit 1
fi

AUTH=(-H "X-API-Key: $COMFY_API_KEY")
JSON=(-H "Content-Type: application/json")

# ── Helpers ──────────────────────────────────────────────────────────────

api_get()  { curl -sS "${AUTH[@]}" "$BASE_URL$1"; }
api_post() { curl -sS "${AUTH[@]}" "${JSON[@]}" -X POST -d "${2:-{}}" "$BASE_URL$1"; }
api_put()  { curl -sS "${AUTH[@]}" "${JSON[@]}" -X PUT  -d "${2:-{}}" "$BASE_URL$1"; }
api_del()  { curl -sS "${AUTH[@]}" -X DELETE "$BASE_URL$1"; }
api_head() { curl -sS "${AUTH[@]}" -o /dev/null -w "%{http_code}" -X HEAD "$BASE_URL$1"; }

pretty() { python3 -m json.tool 2>/dev/null || cat; }

# ── Commands ─────────────────────────────────────────────────────────────

cmd_help() {
  cat <<'EOF'
Comfy Cloud CLI — ./comfy.sh <command> [args]

WORKFLOW
  run <workflow.json>            Submit workflow for execution
  run-with <workflow.json> <node_id> <field> <value>
                                 Submit with one input override

JOBS
  status <job_id>                Get job status
  jobs [--status=X] [--limit=N]  List jobs
  job <job_id>                   Full job detail
  queue                          Show queue
  cancel <job_id> [job_id...]    Cancel pending jobs
  cancel-all                     Clear all pending jobs
  interrupt                      Stop all running jobs

HISTORY
  history [--limit=N]            Execution history
  history-detail <prompt_id>     Detailed history for prompt
  history-delete <id> [id...]    Delete history entries
  history-clear                  Clear all history

FILES (Legacy)
  upload <image_file>            Upload image
  upload-mask <mask_file> <original_ref_json>
  download <filename> [type]     Download output file (saves to ./downloads/)

ASSETS
  assets [--tag=X] [--limit=N]   List assets
  asset <id>                     Get asset details
  asset-upload <file> [--tag=X]  Upload asset file
  asset-upload-url <url> <name> [--tag=X]  Upload asset from URL
  asset-update <id> <json>       Update asset metadata
  asset-delete <id>              Delete asset
  asset-tag-add <id> <tag>...    Add tags
  asset-tag-rm <id> <tag>...     Remove tags
  asset-download-bg <url>        Background download from HF/CivitAI
  asset-exists <hash>            Check if hash exists (200/404)
  tags [--prefix=X]              List all tags

MODELS
  models                         List model folders
  models-in <folder>             List models in folder

NODES & SYSTEM
  nodes                          All available node definitions
  features                       Server feature flags
  stats                          System stats
  user                           Current user info
  userdata <dir/>                List user data files
  userdata-get <file>            Get user data file
  userdata-put <file> <local>    Upload user data file
  userdata-del <file>            Delete user data file

WEBSOCKET
  ws                             Connect to WebSocket (requires wscat)

EOF
}

# ── Workflow ─────────────────────────────────────────────────────────────

cmd_run() {
  local wf_file="${1:?Usage: run <workflow.json>}"
  local payload
  payload=$(python3 -c "
import json, sys
wf = json.load(open('$wf_file'))
print(json.dumps({'prompt': wf}))
")
  api_post "/api/prompt" "$payload" | pretty
}

cmd_run_with() {
  local wf_file="${1:?Usage: run-with <workflow.json> <node_id> <field> <value>}"
  local node_id="${2:?}"
  local field="${3:?}"
  local value="${4:?}"
  local payload
  payload=$(python3 -c "
import json, sys
wf = json.load(open('$wf_file'))
wf['$node_id']['inputs']['$field'] = '$value'
print(json.dumps({'prompt': wf}))
")
  api_post "/api/prompt" "$payload" | pretty
}

# ── Jobs ─────────────────────────────────────────────────────────────────

cmd_status() {
  local job_id="${1:?Usage: status <job_id>}"
  api_get "/api/job/$job_id/status" | pretty
}

cmd_jobs() {
  local query=""
  for arg in "$@"; do
    case "$arg" in
      --status=*) query="${query}&status=${arg#*=}" ;;
      --limit=*)  query="${query}&limit=${arg#*=}" ;;
      --offset=*) query="${query}&offset=${arg#*=}" ;;
      --sort=*)   query="${query}&sort_by=${arg#*=}" ;;
      --order=*)  query="${query}&sort_order=${arg#*=}" ;;
      --type=*)   query="${query}&output_type=${arg#*=}" ;;
    esac
  done
  query="${query#&}"
  api_get "/api/jobs${query:+?$query}" | pretty
}

cmd_job() {
  local job_id="${1:?Usage: job <job_id>}"
  api_get "/api/jobs/$job_id" | pretty
}

cmd_queue() { api_get "/api/queue" | pretty; }

cmd_cancel() {
  local ids=("$@")
  [[ ${#ids[@]} -eq 0 ]] && { echo "Usage: cancel <job_id> [job_id...]"; exit 1; }
  local json_ids
  json_ids=$(printf '"%s",' "${ids[@]}")
  json_ids="[${json_ids%,}]"
  api_post "/api/queue" "{\"delete\": $json_ids}" | pretty
}

cmd_cancel_all() { api_post "/api/queue" '{"clear": true}' | pretty; }
cmd_interrupt() { api_post "/api/interrupt" | pretty; }

# ── History ──────────────────────────────────────────────────────────────

cmd_history() {
  local query=""
  for arg in "$@"; do
    case "$arg" in
      --limit=*)  query="${query}&max_items=${arg#*=}" ;;
      --offset=*) query="${query}&offset=${arg#*=}" ;;
    esac
  done
  query="${query#&}"
  api_get "/api/history_v2${query:+?$query}" | pretty
}

cmd_history_detail() {
  local pid="${1:?Usage: history-detail <prompt_id>}"
  api_get "/api/history_v2/$pid" | pretty
}

cmd_history_delete() {
  local ids=("$@")
  [[ ${#ids[@]} -eq 0 ]] && { echo "Usage: history-delete <id> [id...]"; exit 1; }
  local json_ids
  json_ids=$(printf '"%s",' "${ids[@]}")
  json_ids="[${json_ids%,}]"
  api_post "/api/history" "{\"delete\": $json_ids}" | pretty
}

cmd_history_clear() { api_post "/api/history" '{"clear": true}' | pretty; }

# ── Files ────────────────────────────────────────────────────────────────

cmd_upload() {
  local file="${1:?Usage: upload <image_file>}"
  curl -sS "${AUTH[@]}" -F "image=@$file" "$BASE_URL/api/upload/image" | pretty
}

cmd_upload_mask() {
  local file="${1:?Usage: upload-mask <mask_file> <original_ref_json>}"
  local ref="${2:?}"
  curl -sS "${AUTH[@]}" -F "image=@$file" -F "original_ref=$ref" "$BASE_URL/api/upload/mask" | pretty
}

cmd_download() {
  local filename="${1:?Usage: download <filename> [type]}"
  local type="${2:-output}"
  mkdir -p "$SCRIPT_DIR/downloads"
  local out="$SCRIPT_DIR/downloads/$filename"
  curl -sS "${AUTH[@]}" -L -o "$out" "$BASE_URL/api/view?filename=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$filename'))")&type=$type"
  echo "Saved to $out"
}

# ── Assets ───────────────────────────────────────────────────────────────

cmd_assets() {
  local query=""
  for arg in "$@"; do
    case "$arg" in
      --tag=*)    query="${query}&include_tags=${arg#*=}" ;;
      --limit=*)  query="${query}&limit=${arg#*=}" ;;
      --offset=*) query="${query}&offset=${arg#*=}" ;;
      --sort=*)   query="${query}&sort=${arg#*=}" ;;
      --name=*)   query="${query}&name_contains=${arg#*=}" ;;
    esac
  done
  query="${query#&}"
  api_get "/api/assets${query:+?$query}" | pretty
}

cmd_asset() {
  local id="${1:?Usage: asset <id>}"
  api_get "/api/assets/$id" | pretty
}

cmd_asset_upload() {
  local file="${1:?Usage: asset-upload <file> [--tag=X ...]}"
  shift
  local tag_args=()
  for arg in "$@"; do
    case "$arg" in
      --tag=*) tag_args+=(-F "tags=${arg#*=}") ;;
    esac
  done
  curl -sS "${AUTH[@]}" -F "file=@$file" "${tag_args[@]+"${tag_args[@]}"}" "$BASE_URL/api/assets" | pretty
}

cmd_asset_upload_url() {
  local url="${1:?Usage: asset-upload-url <url> <name> [--tag=X ...]}"
  local name="${2:?}"
  shift 2
  local tags='[]'
  local tag_list=()
  for arg in "$@"; do
    case "$arg" in
      --tag=*) tag_list+=("\"${arg#*=}\"") ;;
    esac
  done
  if [[ ${#tag_list[@]} -gt 0 ]]; then
    tags="[$(IFS=,; echo "${tag_list[*]}")]"
  fi
  api_post "/api/assets" "{\"url\": \"$url\", \"name\": \"$name\", \"tags\": $tags}" | pretty
}

cmd_asset_update() {
  local id="${1:?Usage: asset-update <id> <json>}"
  local json="${2:?}"
  api_put "/api/assets/$id" "$json" | pretty
}

cmd_asset_delete() {
  local id="${1:?Usage: asset-delete <id>}"
  api_del "/api/assets/$id"
  echo "Deleted $id"
}

cmd_asset_tag_add() {
  local id="${1:?Usage: asset-tag-add <id> <tag>...}"
  shift
  local tags=("$@")
  [[ ${#tags[@]} -eq 0 ]] && { echo "Provide at least one tag"; exit 1; }
  local json_tags
  json_tags=$(printf '"%s",' "${tags[@]}")
  json_tags="[${json_tags%,}]"
  api_post "/api/assets/$id/tags" "{\"tags\": $json_tags}" | pretty
}

cmd_asset_tag_rm() {
  local id="${1:?Usage: asset-tag-rm <id> <tag>...}"
  shift
  local tags=("$@")
  [[ ${#tags[@]} -eq 0 ]] && { echo "Provide at least one tag"; exit 1; }
  local json_tags
  json_tags=$(printf '"%s",' "${tags[@]}")
  json_tags="[${json_tags%,}]"
  curl -sS "${AUTH[@]}" "${JSON[@]}" -X DELETE -d "{\"tags\": $json_tags}" "$BASE_URL/api/assets/$id/tags" | pretty
}

cmd_asset_download_bg() {
  local url="${1:?Usage: asset-download-bg <url>}"
  api_post "/api/assets/download" "{\"source_url\": \"$url\"}" | pretty
}

cmd_asset_exists() {
  local hash="${1:?Usage: asset-exists <blake3:hash>}"
  local code
  code=$(api_head "/api/assets/hash/$hash")
  echo "HTTP $code"
}

cmd_tags() {
  local query=""
  for arg in "$@"; do
    case "$arg" in
      --prefix=*) query="${query}&prefix=${arg#*=}" ;;
      --limit=*)  query="${query}&limit=${arg#*=}" ;;
    esac
  done
  query="${query#&}"
  api_get "/api/tags${query:+?$query}" | pretty
}

# ── Models ───────────────────────────────────────────────────────────────

cmd_models() { api_get "/api/experiment/models" | pretty; }

cmd_models_in() {
  local folder="${1:?Usage: models-in <folder>}"
  api_get "/api/experiment/models/$folder" | pretty
}

# ── Nodes & System ──────────────────────────────────────────────────────

cmd_nodes()    { api_get "/api/object_info" | pretty; }
cmd_features() { api_get "/api/features" | pretty; }
cmd_stats()    { api_get "/api/system_stats" | pretty; }
cmd_user()     { api_get "/api/user" | pretty; }

cmd_userdata() {
  local dir="${1:?Usage: userdata <dir/>}"
  api_get "/api/userdata?dir=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$dir'))")" | pretty
}

cmd_userdata_get() {
  local file="${1:?Usage: userdata-get <file>}"
  api_get "/api/userdata/$file"
}

cmd_userdata_put() {
  local file="${1:?Usage: userdata-put <file> <local_path>}"
  local local_path="${2:?}"
  curl -sS "${AUTH[@]}" -H "Content-Type: application/octet-stream" --data-binary "@$local_path" -X POST "$BASE_URL/api/userdata/$file" | pretty
}

cmd_userdata_del() {
  local file="${1:?Usage: userdata-del <file>}"
  api_del "/api/userdata/$file"
  echo "Deleted $file"
}

# ── WebSocket ────────────────────────────────────────────────────────────

cmd_ws() {
  local client_id
  client_id=$(python3 -c "import uuid; print(uuid.uuid4())")
  echo "Connecting as clientId=$client_id ..."
  if command -v wscat &>/dev/null; then
    wscat -c "wss://cloud.comfy.org/ws?clientId=$client_id&token=$COMFY_API_KEY"
  elif command -v websocat &>/dev/null; then
    websocat "wss://cloud.comfy.org/ws?clientId=$client_id&token=$COMFY_API_KEY"
  else
    echo "Install wscat (npm i -g wscat) or websocat to use WebSocket"
    exit 1
  fi
}

# ── Dispatch ─────────────────────────────────────────────────────────────

cmd="${1:-help}"
shift || true

# Normalize command (hyphens to underscores)
cmd_fn="cmd_${cmd//-/_}"

if declare -f "$cmd_fn" > /dev/null 2>&1; then
  "$cmd_fn" "$@"
else
  echo "Unknown command: $cmd"
  echo "Run ./comfy.sh help for usage"
  exit 1
fi
