#!/usr/bin/env bash
# comfy.sh — CLI wrapper for the Comfy Cloud API
# Usage: ./comfy.sh <command> [args...]
#
# Set COMFY_API_KEY env var or create .env file with COMFY_API_KEY=your_key

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_URL="https://cloud.comfy.org"

# Load env vars from .env if not already set
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    value="${value%\"}" && value="${value#\"}"
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < "$SCRIPT_DIR/.env"
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
api_del_body() { curl -sS "${AUTH[@]}" "${JSON[@]}" -X DELETE -d "${2:-{}}" "$BASE_URL$1"; }
api_head() { curl -sS "${AUTH[@]}" -o /dev/null -w "%{http_code}" -X HEAD "$BASE_URL$1"; }

pretty() { python3 -m json.tool 2>/dev/null || cat; }

# ── Shared: Build Payload ────────────────────────────────────────────────
# Builds a prompt submission payload with optional overrides and extra_data.
# Usage: _build_payload <workflow.json> [overrides_json] [extra_data_json]
#   overrides_json: JSON array like [{"node":"3","field":"seed","value":42}]
#   extra_data_json: JSON object merged into extra_data

_build_payload() {
  local wf_file="$1"
  local overrides="${2:-[]}"
  local extra_data="${3:-\{\}}"
  _WF_FILE="$wf_file" _OVERRIDES="$overrides" _EXTRA="$extra_data" python3 -c "
import json, os

wf = json.load(open(os.environ['_WF_FILE']))
overrides = json.loads(os.environ['_OVERRIDES'])
extra = json.loads(os.environ['_EXTRA'])

for o in overrides:
    nid, field, val = str(o['node']), o['field'], o['value']
    if nid in wf and 'inputs' in wf[nid]:
        wf[nid]['inputs'][field] = val

payload = {'prompt': wf}
if extra:
    payload['extra_data'] = extra
print(json.dumps(payload))
"
}

# Parse --set node.field=value into JSON overrides array
_parse_sets() {
  python3 - "$@" <<'PYEOF'
import json, sys

overrides = []
for arg in sys.argv[1:]:
    dot = arg.index('.')
    eq = arg.index('=', dot)
    node = arg[:dot]
    field = arg[dot+1:eq]
    raw = arg[eq+1:]
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        val = raw
    overrides.append({'node': node, 'field': field, 'value': val})
print(json.dumps(overrides))
PYEOF
}

# Extract output filenames from a job detail JSON
_parse_output_filenames() {
  python3 -c "
import json, sys
data = json.load(sys.stdin)
outputs = data.get('outputs', {})
files = []
for node_id, node_out in outputs.items():
    for key in ('images', 'video', 'audio', 'gifs'):
        for item in node_out.get(key, []):
            if 'filename' in item:
                files.append(item['filename'])
for f in files:
    print(f)
"
}

# Download a list of output files to a directory
_download_outputs() {
  local out_dir="$1"
  shift
  local files=("$@")
  mkdir -p "$out_dir"
  for f in "${files[@]}"; do
    local encoded
    encoded=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$f'))")
    curl -sS "${AUTH[@]}" -L -o "$out_dir/$f" "$BASE_URL/api/view?filename=$encoded&type=output"
    echo "  Saved $out_dir/$f"
  done
}

# Poll a job until terminal state, optionally download outputs
_poll_and_download() {
  local job_id="$1"
  local do_download="${2:-false}"
  local out_dir="${3:-$SCRIPT_DIR/downloads}"
  local do_open="${4:-false}"
  local interval="${5:-3}"

  local last_status=""
  while true; do
    local resp
    resp=$(api_get "/api/job/$job_id/status")
    local status
    status=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))")

    if [[ "$status" != "$last_status" ]]; then
      local ts
      ts=$(date +%H:%M:%S)
      if [[ -n "$last_status" ]]; then
        echo "[$ts] $last_status → $status"
      else
        echo "[$ts] $status"
      fi
      last_status="$status"
    fi

    case "$status" in
      completed|success)
        if [[ "$do_download" == "true" ]]; then
          echo "Downloading outputs..."
          local job_detail
          job_detail=$(api_get "/api/jobs/$job_id")
          local -a filenames
          mapfile -t filenames < <(echo "$job_detail" | _parse_output_filenames)
          if [[ ${#filenames[@]} -gt 0 ]]; then
            _download_outputs "$out_dir" "${filenames[@]}"
            if [[ "$do_open" == "true" ]]; then
              for f in "${filenames[@]}"; do
                open "$out_dir/$f" 2>/dev/null || true
              done
            fi
          else
            echo "  No output files found."
          fi
        fi
        return 0
        ;;
      error|failed)
        echo "Job failed."
        echo "$resp" | pretty
        return 1
        ;;
      cancelled)
        echo "Job was cancelled."
        return 1
        ;;
    esac
    sleep "$interval"
  done
}

# ── Commands ─────────────────────────────────────────────────────────────

cmd_help() {
  cat <<'EOF'
Comfy Cloud CLI — ./comfy.sh <command> [args]

WORKFLOW
  run <workflow.json> [--partner-key=X]
                                 Submit workflow for execution
  run-with <workflow.json> --set node.field=value [--set ...] [--partner-key=X]
                                 Submit with input overrides
  run-with <workflow.json> <node_id> <field> <value>
                                 Legacy: single override (backward compat)
  go <workflow.json> [--set ...] [--out=DIR] [--open] [--partner-key=X]
                                 Submit → poll → download in one shot

BATCH
  batch-seed <wf.json> <node_id> <start> <end> [--set ...] [--delay=N]
                                 Run N copies sweeping seed values
  batch-file <wf.json> <node_id> <field> <file.txt> [--set ...]
                                 Run one job per line in a text file
  batch-grid <wf.json> <seeds.txt> <prompts.txt> <seed_node> <prompt_node> <prompt_field>
                                 Cartesian product: every seed × every prompt

PRESETS
  gen --preset=<name> [--prompt "..."] [--seed N] [--set ...] [--open]
                                 Generate from a saved preset
  preset-list                    List saved presets
  preset-show <name>             Show preset details
  preset-save <name> <wf.json> [--desc="..."] [--prompt-node=N.field] [--seed-node=N]
                                 Save a new preset
  preset-delete <name>           Delete a preset

JOBS
  status <job_id>                Get job status
  poll <job_id> [--download] [--out=DIR] [--open]
                                 Poll until done, optionally download
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
  asset-bulk-upload <dir> [--tag=X ...]
                                 Upload all files in a directory
  asset-bulk-tag <tag> <id> [id...]
                                 Add a tag to multiple assets
  asset-bulk-delete <id> [id...] [--yes]
                                 Delete multiple assets
  asset-cleanup [--older-than=30d] [--tag=X] [--dry-run]
                                 Delete old assets matching filters
  asset-search <pattern> [--tag=X] [--limit=N]
                                 Search assets by name

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
  ws                             Connect raw WebSocket (requires wscat/websocat)
  monitor [--job=X] [--save] [--out=DIR]
                                 Pretty-print WebSocket progress stream

IMAGE TO VIDEO
  animate <image> --preset=<name> [--prompt "..."] [--seed N] [--open]
                                 Upload image → run img2vid → download video
  animate <image> <workflow.json> [--set ...] [--open]
                                 Upload image → run custom i2v workflow

EOF
}

# ── Workflow ─────────────────────────────────────────────────────────────

cmd_run() {
  local wf_file=""
  local partner_key="${COMFY_PARTNER_KEY:-}"
  local extra_data="{}"

  for arg in "$@"; do
    case "$arg" in
      --partner-key=*) partner_key="${arg#*=}" ;;
      *) [[ -z "$wf_file" ]] && wf_file="$arg" ;;
    esac
  done
  [[ -z "$wf_file" ]] && { echo "Usage: run <workflow.json>"; exit 1; }

  if [[ -n "$partner_key" ]]; then
    extra_data="{\"api_key_comfy_org\": \"$partner_key\"}"
  fi

  local payload
  payload=$(_build_payload "$wf_file" "[]" "$extra_data")
  api_post "/api/prompt" "$payload" | pretty
}

cmd_run_with() {
  local wf_file=""
  local sets=()
  local partner_key="${COMFY_PARTNER_KEY:-}"

  # Detect legacy mode: run-with file.json node_id field value
  if [[ $# -ge 4 ]] && [[ "${2:-}" != --* ]]; then
    wf_file="$1"
    local node_id="$2" field="$3" raw_value="$4"
    local value
    value=$(python3 -c "
import json
try:
    v = json.loads('$raw_value')
except: v = '$raw_value'
print(json.dumps(v))
")
    local overrides="[{\"node\":\"$node_id\",\"field\":\"$field\",\"value\":$value}]"
    local payload
    payload=$(_build_payload "$wf_file" "$overrides" "{}")
    api_post "/api/prompt" "$payload" | pretty
    return
  fi

  # Modern mode: --set node.field=value
  for arg in "$@"; do
    case "$arg" in
      --set=*) sets+=("${arg#*=}") ;;
      --set)   ;; # next arg handled below
      --partner-key=*) partner_key="${arg#*=}" ;;
      *) [[ -z "$wf_file" ]] && wf_file="$arg" ;;
    esac
  done
  # Handle --set as separate arg (--set node.field=val)
  local prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--set" ]]; then
      sets+=("$arg")
    fi
    prev="$arg"
  done

  [[ -z "$wf_file" ]] && { echo "Usage: run-with <wf.json> --set node.field=value [--set ...]"; exit 1; }

  local overrides
  overrides=$(_parse_sets "${sets[@]}")

  local extra_data="{}"
  if [[ -n "$partner_key" ]]; then
    extra_data="{\"api_key_comfy_org\": \"$partner_key\"}"
  fi

  local payload
  payload=$(_build_payload "$wf_file" "$overrides" "$extra_data")
  api_post "/api/prompt" "$payload" | pretty
}

# ── Go: Submit + Poll + Download ─────────────────────────────────────────

cmd_go() {
  local wf_file=""
  local sets=()
  local partner_key="${COMFY_PARTNER_KEY:-}"
  local out_dir="$SCRIPT_DIR/downloads"
  local do_open="false"

  local prev=""
  for arg in "$@"; do
    case "$arg" in
      --set=*) sets+=("${arg#*=}") ;;
      --set)   ;;
      --partner-key=*) partner_key="${arg#*=}" ;;
      --out=*) out_dir="${arg#*=}" ;;
      --open)  do_open="true" ;;
      *)
        if [[ "$prev" == "--set" ]]; then
          sets+=("$arg")
        elif [[ -z "$wf_file" ]]; then
          wf_file="$arg"
        fi
        ;;
    esac
    prev="$arg"
  done

  [[ -z "$wf_file" ]] && { echo "Usage: go <workflow.json> [--set ...] [--out=DIR] [--open]"; exit 1; }

  local overrides="[]"
  if [[ ${#sets[@]} -gt 0 ]]; then
    overrides=$(_parse_sets "${sets[@]}")
  fi

  local extra_data="{}"
  if [[ -n "$partner_key" ]]; then
    extra_data="{\"api_key_comfy_org\": \"$partner_key\"}"
  fi

  local payload
  payload=$(_build_payload "$wf_file" "$overrides" "$extra_data")

  echo "Submitting workflow..."
  local resp
  resp=$(api_post "/api/prompt" "$payload")
  local prompt_id
  prompt_id=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt_id',''))")

  if [[ -z "$prompt_id" ]]; then
    echo "Submit failed:"
    echo "$resp" | pretty
    return 1
  fi

  echo "Job: $prompt_id"
  _poll_and_download "$prompt_id" "true" "$out_dir" "$do_open"
}

# ── Poll ─────────────────────────────────────────────────────────────────

cmd_poll() {
  local job_id=""
  local do_download="false"
  local out_dir="$SCRIPT_DIR/downloads"
  local do_open="false"

  for arg in "$@"; do
    case "$arg" in
      --download) do_download="true" ;;
      --out=*)    out_dir="${arg#*=}" ;;
      --open)     do_open="true"; do_download="true" ;;
      *)          [[ -z "$job_id" ]] && job_id="$arg" ;;
    esac
  done

  [[ -z "$job_id" ]] && { echo "Usage: poll <job_id> [--download] [--out=DIR] [--open]"; exit 1; }
  _poll_and_download "$job_id" "$do_download" "$out_dir" "$do_open"
}

# ── Batch Runs ───────────────────────────────────────────────────────────

cmd_batch_seed() {
  local wf_file=""
  local node_id=""
  local start=""
  local end=""
  local sets=()
  local delay=0
  local positional=()

  local prev=""
  for arg in "$@"; do
    case "$arg" in
      --set=*) sets+=("${arg#*=}") ;;
      --set)   ;;
      --delay=*) delay="${arg#*=}" ;;
      *)
        if [[ "$prev" == "--set" ]]; then
          sets+=("$arg")
        else
          positional+=("$arg")
        fi
        ;;
    esac
    prev="$arg"
  done

  wf_file="${positional[0]:-}"
  node_id="${positional[1]:-}"
  start="${positional[2]:-}"
  end="${positional[3]:-}"

  [[ -z "$end" ]] && { echo "Usage: batch-seed <wf.json> <node_id> <start> <end> [--set ...] [--delay=N]"; exit 1; }

  local base_overrides="[]"
  if [[ ${#sets[@]} -gt 0 ]]; then
    base_overrides=$(_parse_sets "${sets[@]}")
  fi

  local ids=()
  for ((seed=start; seed<=end; seed++)); do
    local overrides
    overrides=$(_BASE="$base_overrides" _NID="$node_id" _SEED="$seed" python3 -c "
import json, os
base = json.loads(os.environ['_BASE'])
base.append({'node': os.environ['_NID'], 'field': 'seed', 'value': int(os.environ['_SEED'])})
print(json.dumps(base))
")
    local payload
    payload=$(_build_payload "$wf_file" "$overrides" "{}")
    local resp
    resp=$(api_post "/api/prompt" "$payload")
    local pid
    pid=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt_id','ERROR'))")
    echo "seed=$seed → $pid"
    ids+=("$pid")
    [[ "$delay" -gt 0 ]] && sleep "$delay"
  done

  echo ""
  echo "Submitted ${#ids[@]} jobs:"
  printf '%s\n' "${ids[@]}"
}

cmd_batch_file() {
  local positional=()
  local sets=()

  local prev=""
  for arg in "$@"; do
    case "$arg" in
      --set=*) sets+=("${arg#*=}") ;;
      --set)   ;;
      *)
        if [[ "$prev" == "--set" ]]; then
          sets+=("$arg")
        else
          positional+=("$arg")
        fi
        ;;
    esac
    prev="$arg"
  done

  local wf_file="${positional[0]:-}"
  local node_id="${positional[1]:-}"
  local field="${positional[2]:-}"
  local list_file="${positional[3]:-}"

  [[ -z "$list_file" ]] && { echo "Usage: batch-file <wf.json> <node_id> <field> <file.txt> [--set ...]"; exit 1; }

  local base_overrides="[]"
  if [[ ${#sets[@]} -gt 0 ]]; then
    base_overrides=$(_parse_sets "${sets[@]}")
  fi

  local ids=()
  local line_num=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    line_num=$((line_num + 1))
    local overrides
    overrides=$(_BASE="$base_overrides" _NID="$node_id" _FIELD="$field" _VAL="$line" python3 -c "
import json, os
base = json.loads(os.environ['_BASE'])
base.append({'node': os.environ['_NID'], 'field': os.environ['_FIELD'], 'value': os.environ['_VAL']})
print(json.dumps(base))
")
    local payload
    payload=$(_build_payload "$wf_file" "$overrides" "{}")
    local resp
    resp=$(api_post "/api/prompt" "$payload")
    local pid
    pid=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt_id','ERROR'))")
    echo "[$line_num] \"$line\" → $pid"
    ids+=("$pid")
  done < "$list_file"

  echo ""
  echo "Submitted ${#ids[@]} jobs:"
  printf '%s\n' "${ids[@]}"
}

cmd_batch_grid() {
  local wf_file="${1:?Usage: batch-grid <wf.json> <seeds.txt> <prompts.txt> <seed_node> <prompt_node> <prompt_field>}"
  local seeds_file="${2:?}"
  local prompts_file="${3:?}"
  local seed_node="${4:?}"
  local prompt_node="${5:?}"
  local prompt_field="${6:?}"

  local ids=()
  local count=0

  while IFS= read -r seed || [[ -n "$seed" ]]; do
    [[ -z "$seed" || "$seed" =~ ^# ]] && continue
    while IFS= read -r prompt || [[ -n "$prompt" ]]; do
      [[ -z "$prompt" || "$prompt" =~ ^# ]] && continue
      count=$((count + 1))
      local overrides
      overrides=$(_SNODE="$seed_node" _SEED="$seed" _PNODE="$prompt_node" _PFIELD="$prompt_field" _PROMPT="$prompt" python3 -c "
import json, os
print(json.dumps([
    {'node': os.environ['_SNODE'], 'field': 'seed', 'value': int(os.environ['_SEED'])},
    {'node': os.environ['_PNODE'], 'field': os.environ['_PFIELD'], 'value': os.environ['_PROMPT']}
]))
")
      local payload
      payload=$(_build_payload "$wf_file" "$overrides" "{}")
      local resp
      resp=$(api_post "/api/prompt" "$payload")
      local pid
      pid=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt_id','ERROR'))")
      echo "[$count] seed=$seed prompt=\"$prompt\" → $pid"
      ids+=("$pid")
    done < "$prompts_file"
  done < "$seeds_file"

  echo ""
  echo "Submitted ${#ids[@]} jobs (grid: seeds × prompts):"
  printf '%s\n' "${ids[@]}"
}

# ── Presets ──────────────────────────────────────────────────────────────

PRESETS_DIR="$SCRIPT_DIR/presets"

cmd_preset_list() {
  if [[ ! -d "$PRESETS_DIR" ]] || [[ -z "$(ls -A "$PRESETS_DIR"/*.json 2>/dev/null)" ]]; then
    echo "No presets saved. Create one with: preset-save <name> <workflow.json>"
    return
  fi
  echo "Available presets:"
  for f in "$PRESETS_DIR"/*.json; do
    local name
    name=$(basename "$f" .json)
    local desc
    desc=$(python3 -c "import json; d=json.load(open('$f')); print(d.get('description',''))")
    printf "  %-20s %s\n" "$name" "$desc"
  done
}

cmd_preset_show() {
  local name="${1:?Usage: preset-show <name>}"
  local pfile="$PRESETS_DIR/$name.json"
  [[ -f "$pfile" ]] || { echo "Preset '$name' not found"; exit 1; }
  cat "$pfile" | pretty
}

cmd_preset_save() {
  local name=""
  local wf_file=""
  local desc=""
  local prompt_node_field=""
  local seed_node=""
  local image_node=""
  local positional=()

  for arg in "$@"; do
    case "$arg" in
      --desc=*) desc="${arg#*=}" ;;
      --prompt-node=*) prompt_node_field="${arg#*=}" ;;
      --seed-node=*) seed_node="${arg#*=}" ;;
      --image-node=*) image_node="${arg#*=}" ;;
      *) positional+=("$arg") ;;
    esac
  done

  name="${positional[0]:-}"
  wf_file="${positional[1]:-}"
  [[ -z "$wf_file" ]] && { echo "Usage: preset-save <name> <wf.json> [--desc=...] [--prompt-node=N.field] [--seed-node=N] [--image-node=N]"; exit 1; }

  mkdir -p "$PRESETS_DIR"

  # Copy workflow into presets dir
  cp "$wf_file" "$PRESETS_DIR/${name}_workflow.json"

  # Split prompt_node_field into node and field
  local p_node="" p_field=""
  if [[ "$prompt_node_field" == *.* ]]; then
    p_node="${prompt_node_field%%.*}"
    p_field="${prompt_node_field#*.}"
  fi

  _WF="${name}_workflow.json" _DESC="$desc" _PN="$p_node" _PF="$p_field" _SN="$seed_node" _IN="$image_node" _OUT="$PRESETS_DIR/$name.json" python3 -c "
import json, os
preset = {
    'workflow': os.environ['_WF'],
    'description': os.environ['_DESC'],
    'prompt_node': os.environ['_PN'],
    'prompt_field': os.environ['_PF'],
    'seed_node': os.environ['_SN'],
    'image_node': os.environ['_IN'],
    'defaults': []
}
preset = {k: v for k, v in preset.items() if v != '' or k == 'defaults'}
with open(os.environ['_OUT'], 'w') as f:
    json.dump(preset, f, indent=2)
"
  echo "Preset saved: $name"
}

cmd_preset_delete() {
  local name="${1:?Usage: preset-delete <name>}"
  local pfile="$PRESETS_DIR/$name.json"
  [[ -f "$pfile" ]] || { echo "Preset '$name' not found"; exit 1; }
  rm -f "$pfile" "$PRESETS_DIR/${name}_workflow.json"
  echo "Deleted preset: $name"
}

cmd_gen() {
  local preset_name=""
  local prompt_text=""
  local seed_val=""
  local sets=()
  local do_open="false"
  local out_dir="$SCRIPT_DIR/downloads"
  local partner_key="${COMFY_PARTNER_KEY:-}"

  local prev=""
  for arg in "$@"; do
    case "$arg" in
      --preset=*) preset_name="${arg#*=}" ;;
      --prompt=*) prompt_text="${arg#*=}" ;;
      --prompt)   ;; # next arg
      --seed=*)   seed_val="${arg#*=}" ;;
      --set=*)    sets+=("${arg#*=}") ;;
      --set)      ;;
      --open)     do_open="true" ;;
      --out=*)    out_dir="${arg#*=}" ;;
      --partner-key=*) partner_key="${arg#*=}" ;;
      *)
        if [[ "$prev" == "--prompt" ]]; then
          prompt_text="$arg"
        elif [[ "$prev" == "--set" ]]; then
          sets+=("$arg")
        fi
        ;;
    esac
    prev="$arg"
  done

  [[ -z "$preset_name" ]] && { echo "Usage: gen --preset=<name> [--prompt \"...\"] [--seed N] [--set ...]"; exit 1; }

  local pfile="$PRESETS_DIR/$preset_name.json"
  [[ -f "$pfile" ]] || { echo "Preset '$preset_name' not found. Run: preset-list"; exit 1; }

  # Load preset and build overrides
  local overrides
  overrides=$(_PFILE="$pfile" _PROMPT="$prompt_text" _SEED="$seed_val" python3 -c "
import json, os, random

preset = json.load(open(os.environ['_PFILE']))
overrides = list(preset.get('defaults', []))

prompt_text = os.environ['_PROMPT']
seed_val = os.environ['_SEED']
prompt_node = preset.get('prompt_node', '')
prompt_field = preset.get('prompt_field', '')
seed_node = preset.get('seed_node', '')

if prompt_text and prompt_node and prompt_field:
    overrides.append({'node': prompt_node, 'field': prompt_field, 'value': prompt_text})

if seed_node:
    if seed_val == '' or seed_val == 'random':
        sv = random.randint(0, 2**32 - 1)
    else:
        sv = int(seed_val)
    overrides.append({'node': seed_node, 'field': 'seed', 'value': sv})

print(json.dumps(overrides))
")

  local wf_rel
  wf_rel=$(_PFILE="$pfile" python3 -c "import json, os; print(json.load(open(os.environ['_PFILE']))['workflow'])")
  local wf_file="$PRESETS_DIR/$wf_rel"

  # Add any --set overrides
  if [[ ${#sets[@]} -gt 0 ]]; then
    local extra_sets
    extra_sets=$(_parse_sets "${sets[@]}")
    overrides=$(_A="$overrides" _B="$extra_sets" python3 -c "
import json, os
a = json.loads(os.environ['_A'])
b = json.loads(os.environ['_B'])
print(json.dumps(a + b))
")
  fi

  local extra_data="{}"
  if [[ -n "$partner_key" ]]; then
    extra_data="{\"api_key_comfy_org\": \"$partner_key\"}"
  fi

  local payload
  payload=$(_build_payload "$wf_file" "$overrides" "$extra_data")

  echo "Generating with preset: $preset_name"
  local resp
  resp=$(api_post "/api/prompt" "$payload")
  local prompt_id
  prompt_id=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt_id',''))")

  if [[ -z "$prompt_id" ]]; then
    echo "Submit failed:"
    echo "$resp" | pretty
    return 1
  fi

  echo "Job: $prompt_id"
  _poll_and_download "$prompt_id" "true" "$out_dir" "$do_open"
}

# ── Animate (Image to Video) ─────────────────────────────────────────────

cmd_animate() {
  local image_file=""
  local preset_name=""
  local wf_file=""
  local prompt_text=""
  local seed_val=""
  local sets=()
  local do_open="false"
  local out_dir="$SCRIPT_DIR/downloads"
  local partner_key="${COMFY_PARTNER_KEY:-}"

  local prev=""
  for arg in "$@"; do
    case "$arg" in
      --preset=*) preset_name="${arg#*=}" ;;
      --prompt=*) prompt_text="${arg#*=}" ;;
      --prompt)   ;;
      --seed=*)   seed_val="${arg#*=}" ;;
      --set=*)    sets+=("${arg#*=}") ;;
      --set)      ;;
      --open)     do_open="true" ;;
      --out=*)    out_dir="${arg#*=}" ;;
      --partner-key=*) partner_key="${arg#*=}" ;;
      *)
        if [[ "$prev" == "--prompt" ]]; then
          prompt_text="$arg"
        elif [[ "$prev" == "--set" ]]; then
          sets+=("$arg")
        elif [[ -z "$image_file" ]]; then
          image_file="$arg"
        elif [[ -z "$wf_file" && -z "$preset_name" ]]; then
          wf_file="$arg"
        fi
        ;;
    esac
    prev="$arg"
  done

  [[ -z "$image_file" ]] && { echo "Usage: animate <image> --preset=<name> [--prompt \"...\"] [--open]"; exit 1; }
  [[ -z "$preset_name" && -z "$wf_file" ]] && { echo "Provide --preset=<name> or a workflow JSON"; exit 1; }

  # Step 1: Upload the image
  echo "Uploading image: $image_file"
  local upload_resp
  upload_resp=$(curl -sS "${AUTH[@]}" -F "image=@$image_file" "$BASE_URL/api/upload/image")
  local uploaded_name
  uploaded_name=$(echo "$upload_resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))")

  if [[ -z "$uploaded_name" ]]; then
    echo "Upload failed:"
    echo "$upload_resp" | pretty
    return 1
  fi
  echo "Uploaded as: $uploaded_name"

  # Step 2: Build the workflow with image reference
  local overrides=""
  if [[ -n "$preset_name" ]]; then
    local pfile="$PRESETS_DIR/$preset_name.json"
    [[ -f "$pfile" ]] || { echo "Preset '$preset_name' not found. Run: preset-list"; exit 1; }

    overrides=$(_PFILE="$pfile" _PROMPT="$prompt_text" _SEED="$seed_val" python3 -c "
import json, os, random

preset = json.load(open(os.environ['_PFILE']))
overrides = list(preset.get('defaults', []))

prompt_text = os.environ['_PROMPT']
seed_val = os.environ['_SEED']
prompt_node = preset.get('prompt_node', '')
prompt_field = preset.get('prompt_field', '')
seed_node = preset.get('seed_node', '')
image_node = preset.get('image_node', '')
image_field = preset.get('image_field', 'image')

if prompt_text and prompt_node and prompt_field:
    overrides.append({'node': prompt_node, 'field': prompt_field, 'value': prompt_text})

if seed_node:
    if seed_val == '' or seed_val == 'random':
        sv = random.randint(0, 2**32 - 1)
    else:
        sv = int(seed_val)
    overrides.append({'node': seed_node, 'field': 'seed', 'value': sv})

print(json.dumps(overrides))
")
    local wf_rel
    wf_rel=$(_PFILE="$pfile" python3 -c "import json, os; print(json.load(open(os.environ['_PFILE']))['workflow'])")
    wf_file="$PRESETS_DIR/$wf_rel"

    # Get image_node from preset
    local image_node
    image_node=$(_PFILE="$pfile" python3 -c "import json, os; print(json.load(open(os.environ['_PFILE'])).get('image_node', '5'))")
  else
    overrides="[]"
    # Default: image node is "5" (convention in our i2v workflows)
    local image_node="5"
  fi

  # Add image override
  overrides=$(_A="$overrides" _INODE="$image_node" _INAME="$uploaded_name" python3 -c "
import json, os
a = json.loads(os.environ['_A'])
a.append({'node': os.environ['_INODE'], 'field': 'image', 'value': os.environ['_INAME']})
print(json.dumps(a))
")

  # Add any --set overrides
  if [[ ${#sets[@]} -gt 0 ]]; then
    local extra_sets
    extra_sets=$(_parse_sets "${sets[@]}")
    overrides=$(_A="$overrides" _B="$extra_sets" python3 -c "
import json, os
a = json.loads(os.environ['_A'])
b = json.loads(os.environ['_B'])
print(json.dumps(a + b))
")
  fi

  local extra_data="{}"
  if [[ -n "$partner_key" ]]; then
    extra_data="{\"api_key_comfy_org\": \"$partner_key\"}"
  fi

  local payload
  payload=$(_build_payload "$wf_file" "$overrides" "$extra_data")

  echo "Animating..."
  local resp
  resp=$(api_post "/api/prompt" "$payload")
  local prompt_id
  prompt_id=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin).get('prompt_id',''))")

  if [[ -z "$prompt_id" ]]; then
    echo "Submit failed:"
    echo "$resp" | pretty
    return 1
  fi

  echo "Job: $prompt_id"
  _poll_and_download "$prompt_id" "true" "$out_dir" "$do_open"
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
  api_del_body "/api/assets/$id/tags" "{\"tags\": $json_tags}" | pretty
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

# ── Bulk Asset Management ────────────────────────────────────────────────

cmd_asset_bulk_upload() {
  local dir=""
  local tag_args=()

  for arg in "$@"; do
    case "$arg" in
      --tag=*) tag_args+=(-F "tags=${arg#*=}") ;;
      *) [[ -z "$dir" ]] && dir="$arg" ;;
    esac
  done

  [[ -z "$dir" ]] && { echo "Usage: asset-bulk-upload <directory> [--tag=X ...]"; exit 1; }

  local files=("$dir"/*)
  local total=${#files[@]}
  local i=0

  for f in "${files[@]}"; do
    [[ -f "$f" ]] || continue
    i=$((i + 1))
    echo "[$i/$total] Uploading $(basename "$f")..."
    curl -sS "${AUTH[@]}" -F "file=@$f" "${tag_args[@]+"${tag_args[@]}"}" "$BASE_URL/api/assets" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  → ' + d.get('id','?') + ' (' + d.get('name','') + ')')" 2>/dev/null || echo "  → failed"
  done
  echo "Done. Uploaded $i files."
}

cmd_asset_bulk_tag() {
  local tag="${1:?Usage: asset-bulk-tag <tag> <id> [id...]}"
  shift
  local ids=("$@")
  [[ ${#ids[@]} -eq 0 ]] && { echo "Provide at least one asset ID"; exit 1; }

  for id in "${ids[@]}"; do
    api_post "/api/assets/$id/tags" "{\"tags\": [\"$tag\"]}" | python3 -c "import json,sys; d=json.load(sys.stdin); print('$id → tags: ' + ', '.join(d.get('total_tags',[])))" 2>/dev/null || echo "$id → failed"
  done
}

cmd_asset_bulk_delete() {
  local ids=()
  local skip_confirm="false"

  for arg in "$@"; do
    case "$arg" in
      --yes) skip_confirm="true" ;;
      *) ids+=("$arg") ;;
    esac
  done

  [[ ${#ids[@]} -eq 0 ]] && { echo "Usage: asset-bulk-delete <id> [id...] [--yes]"; exit 1; }

  if [[ "$skip_confirm" != "true" ]]; then
    echo "About to delete ${#ids[@]} assets. Continue? (y/N)"
    read -r confirm
    [[ "$confirm" =~ ^[yY] ]] || { echo "Cancelled."; return; }
  fi

  for id in "${ids[@]}"; do
    api_del "/api/assets/$id" 2>/dev/null && echo "Deleted $id" || echo "Failed: $id"
  done
}

cmd_asset_cleanup() {
  local older_than=""
  local tag_filter=""
  local dry_run="false"

  for arg in "$@"; do
    case "$arg" in
      --older-than=*) older_than="${arg#*=}" ;;
      --tag=*) tag_filter="${arg#*=}" ;;
      --dry-run) dry_run="true" ;;
    esac
  done

  # Fetch assets with optional tag filter
  local query=""
  [[ -n "$tag_filter" ]] && query="&include_tags=$tag_filter"
  query="limit=500${query}"

  local resp
  resp=$(api_get "/api/assets?$query")

  # Filter by age and collect IDs
  local to_delete
  to_delete=$(echo "$resp" | python3 -c "
import json, sys
from datetime import datetime, timedelta, timezone

data = json.load(sys.stdin)
assets = data.get('assets', [])

older_than = '$older_than'
if not older_than:
    # default 30 days
    older_than = '30d'

num = int(older_than[:-1])
unit = older_than[-1]
if unit == 'd':
    delta = timedelta(days=num)
elif unit == 'h':
    delta = timedelta(hours=num)
else:
    delta = timedelta(days=num)

cutoff = datetime.now(timezone.utc) - delta
to_delete = []

for a in assets:
    created = a.get('created_at', '')
    try:
        dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
        if dt < cutoff:
            to_delete.append({'id': a['id'], 'name': a.get('name',''), 'created': created})
    except:
        pass

for item in to_delete:
    print(json.dumps(item))
")

  if [[ -z "$to_delete" ]]; then
    echo "No assets matching cleanup criteria."
    return
  fi

  local count=0
  while IFS= read -r line; do
    local id name
    id=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
    name=$(echo "$line" | python3 -c "import json,sys; print(json.load(sys.stdin)['name'])")
    count=$((count + 1))
    if [[ "$dry_run" == "true" ]]; then
      echo "[dry-run] Would delete: $name ($id)"
    else
      api_del "/api/assets/$id" 2>/dev/null && echo "Deleted: $name ($id)" || echo "Failed: $name ($id)"
    fi
  done <<< "$to_delete"

  if [[ "$dry_run" == "true" ]]; then
    echo "Dry run: $count assets would be deleted."
  else
    echo "Cleaned up $count assets."
  fi
}

cmd_asset_search() {
  local pattern=""
  local query=""

  for arg in "$@"; do
    case "$arg" in
      --tag=*)   query="${query}&include_tags=${arg#*=}" ;;
      --limit=*) query="${query}&limit=${arg#*=}" ;;
      *) [[ -z "$pattern" ]] && pattern="$arg" ;;
    esac
  done

  [[ -z "$pattern" ]] && { echo "Usage: asset-search <name_pattern> [--tag=X] [--limit=N]"; exit 1; }

  query="name_contains=$pattern${query}"
  local resp
  resp=$(api_get "/api/assets?$query")

  echo "$resp" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assets = data.get('assets', [])
if not assets:
    print('No assets found.')
    sys.exit()
print(f'Found {data.get(\"total\", len(assets))} assets:')
print(f'{\"ID\":<38} {\"Name\":<40} {\"Tags\"}')
print('-' * 100)
for a in assets:
    tags = ', '.join(a.get('tags', []))
    print(f'{a[\"id\"]:<38} {a.get(\"name\",\"\"):<40} {tags}')
"
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

cmd_monitor() {
  local filter_job=""
  local do_save="false"
  local out_dir="$SCRIPT_DIR/downloads"

  for arg in "$@"; do
    case "$arg" in
      --job=*) filter_job="${arg#*=}" ;;
      --save)  do_save="true" ;;
      --out=*) out_dir="${arg#*=}" ;;
    esac
  done

  local client_id
  client_id=$(python3 -c "import uuid; print(uuid.uuid4())")

  local ws_cmd=""
  if command -v wscat &>/dev/null; then
    ws_cmd="wscat -c wss://cloud.comfy.org/ws?clientId=$client_id&token=$COMFY_API_KEY"
  elif command -v websocat &>/dev/null; then
    ws_cmd="websocat wss://cloud.comfy.org/ws?clientId=$client_id&token=$COMFY_API_KEY"
  else
    echo "Install wscat (npm i -g wscat) or websocat to use WebSocket"
    exit 1
  fi

  echo "Monitoring... (Ctrl+C to stop)"
  [[ -n "$filter_job" ]] && echo "Filtering for job: $filter_job"

  eval "$ws_cmd" 2>/dev/null | python3 -u -c "
import json, sys, time

RESET = '\033[0m'
GREEN = '\033[32m'
RED = '\033[31m'
YELLOW = '\033[33m'
CYAN = '\033[36m'
DIM = '\033[2m'

filter_job = '$filter_job'
do_save = '$do_save' == 'true'
output_files = []

def ts():
    return time.strftime('%H:%M:%S')

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue

    mtype = msg.get('type', '')
    data = msg.get('data', {})
    pid = data.get('prompt_id', '')

    if filter_job and pid and pid != filter_job:
        continue

    if mtype == 'status':
        q = data.get('status', {}).get('exec_info', {}).get('queue_remaining', 0)
        print(f'{DIM}[{ts()}]{RESET} Queue: {q} remaining')

    elif mtype == 'execution_start':
        print(f'{CYAN}[{ts()}]{RESET} Workflow started: {pid}')

    elif mtype == 'executing':
        node = data.get('node')
        if node:
            print(f'{CYAN}[{ts()}]{RESET} Executing node: {node}')
        else:
            print(f'{GREEN}[{ts()}]{RESET} Execution complete')

    elif mtype == 'progress':
        val = data.get('value', 0)
        mx = data.get('max', 1)
        node = data.get('node', '?')
        bar_len = 30
        filled = int(bar_len * val / mx) if mx > 0 else 0
        bar = '=' * filled + '>' + ' ' * (bar_len - filled - 1)
        print(f'{YELLOW}[{ts()}]{RESET} [{bar}] {val}/{mx} (node {node})', end='\r')
        if val >= mx:
            print()

    elif mtype == 'executed':
        node = data.get('node', '?')
        output = data.get('output', {})
        parts = []
        for key in ('images', 'video', 'audio', 'gifs'):
            items = output.get(key, [])
            if items:
                parts.append(f'{len(items)} {key}')
                for item in items:
                    fn = item.get('filename')
                    if fn:
                        output_files.append(fn)
        summary = ', '.join(parts) if parts else 'done'
        print(f'{GREEN}[{ts()}]{RESET} Node {node} output: {summary}')

    elif mtype == 'execution_success':
        print(f'{GREEN}[{ts()}] Workflow complete!{RESET}')
        if do_save and output_files:
            print(f'Output files: {output_files}')

    elif mtype == 'execution_error':
        etype = data.get('exception_type', 'Unknown')
        emsg = data.get('exception_message', '')
        node = data.get('node_id', '?')
        print(f'{RED}[{ts()}] ERROR in node {node}: {etype}{RESET}')
        print(f'{RED}  {emsg}{RESET}')

    elif mtype == 'execution_cached':
        nodes = data.get('nodes', [])
        print(f'{DIM}[{ts()}]{RESET} Cached: {len(nodes)} nodes skipped')

    elif mtype == 'execution_interrupted':
        print(f'{YELLOW}[{ts()}] Interrupted{RESET}')
"

  # If --save was set and we got output files, download them
  if [[ "$do_save" == "true" ]]; then
    echo "Note: Use 'go' command for automatic download, or 'download <filename>' for individual files."
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
