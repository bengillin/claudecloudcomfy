# Comfy Cloud API — Complete Reference

**Base URL:** `https://cloud.comfy.org`
**Auth:** `X-API-Key` header (generate at https://platform.comfy.org/profile/api-keys)
**WebSocket:** `wss://cloud.comfy.org/ws?clientId={uuid}&token={api_key}`
**Status:** Experimental — may change without notice

---

## Endpoint Map

### Core Workflow
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/prompt` | Submit workflow for execution → `{prompt_id}` |
| GET | `/api/prompt` | Get current prompt/queue info |
| GET | `/api/workflow_templates` | Get available workflow templates (public) |
| GET | `/api/global_subgraphs` | List subgraph blueprints (public) |
| GET | `/api/global_subgraphs/{id}` | Get specific subgraph data (public) |

### Jobs
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/job/{job_id}/status` | Poll job status |
| GET | `/api/jobs` | List jobs (paginated, filterable) |
| GET | `/api/jobs/{job_id}` | Full job detail (includes workflow + outputs) |
| GET | `/api/queue` | Get running + pending queue |
| POST | `/api/queue` | Cancel pending jobs by ID or clear all |
| POST | `/api/interrupt` | Cancel all running jobs |

### History
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/history_v2` | Paginated execution history |
| GET | `/api/history_v2/{prompt_id}` | History for specific prompt |
| POST | `/api/history` | Delete history entries or clear all |

### Files (Legacy, ComfyUI-compatible)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload/image` | Upload image (multipart) |
| POST | `/api/upload/mask` | Upload mask image |
| GET | `/api/view` | Download output file (302 → signed URL) |
| GET | `/api/files/mask-layers` | Get related mask layers |

### Assets (Modern API)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/assets` | List assets (paginated, tag-filterable) |
| POST | `/api/assets` | Upload asset (multipart or URL) |
| GET | `/api/assets/{id}` | Get asset details |
| PUT | `/api/assets/{id}` | Update asset metadata |
| DELETE | `/api/assets/{id}` | Delete asset |
| POST | `/api/assets/{id}/tags` | Add tags to asset |
| DELETE | `/api/assets/{id}/tags` | Remove tags from asset |
| POST | `/api/assets/from-hash` | Create asset ref from existing hash |
| GET | `/api/assets/remote-metadata` | Preview metadata from URL (CivitAI, HF) |
| POST | `/api/assets/download` | Background download from HF/CivitAI |
| HEAD | `/api/assets/hash/{hash}` | Check if content exists by hash |
| GET | `/api/tags` | List all tags with counts |
| GET | `/api/assets/tags/refine` | Tag histogram for filtered assets |

### Models
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/experiment/models` | List model folders (public) |
| GET | `/api/experiment/models/{folder}` | List models in folder (public) |
| GET | `/api/experiment/models/preview/{folder}/{path_index}/{filename}` | Model preview image (public) |

### User & System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/user` | Current user info |
| GET | `/api/userdata?dir=workflows/` | List user data files |
| GET | `/api/userdata/{file}` | Get user data file |
| POST | `/api/userdata/{file}` | Upload user data file |
| DELETE | `/api/userdata/{file}` | Delete user data file |
| GET | `/api/object_info` | All available node definitions |
| GET | `/api/features` | Server feature flags |
| GET | `/api/system_stats` | System stats (public) |

---

## Job Statuses
`waiting_to_dispatch` → `pending` → `in_progress` → `completed` | `error` | `cancelled`

## HTTP Error Codes
| Code | Meaning |
|------|---------|
| 400 | Invalid request |
| 401 | Missing/invalid API key |
| 402 | Insufficient credits |
| 429 | Inactive subscription |
| 403 | Forbidden (wrong user) |
| 404 | Not found |
| 413 | File too large |
| 500 | Server error |
| 503 | Service unavailable |

## Execution Error Types
`ValidationError`, `ModelDownloadError`, `ImageDownloadError`, `OOMError`, `PanicError`, `ServiceError`, `WebSocketError`, `DispatcherError`, `InsufficientFundsError`, `InactiveSubscriptionError`

## Cloud vs Local ComfyUI Differences
- `subfolder` — ignored (content-addressed storage by hash)
- `overwrite` — ignored (hash dedup)
- `number`, `front` — ignored (cloud fair scheduling)
- `split`, `full_info` — ignored (always full metadata)

## WebSocket Message Types
| Type | When |
|------|------|
| `status` | Queue state changed |
| `execution_start` | Workflow began |
| `execution_cached` | Nodes skipped (cached) |
| `executing` | Node started (null = done) |
| `progress` | Step progress (value/max) |
| `progress_state` | Extended progress metadata |
| `executed` | Node completed with outputs |
| `execution_success` | Workflow completed |
| `execution_error` | Workflow failed |
| `execution_interrupted` | User cancelled |

## Binary WebSocket Messages (big-endian)
- **Type 1 (PREVIEW_IMAGE):** `[type:4B][image_type:4B][image_data]`
- **Type 3 (TEXT):** `[type:4B][node_id_len:4B][node_id][text]`
- **Type 4 (PREVIEW_WITH_METADATA):** `[type:4B][metadata_len:4B][metadata_json][image_data]`
