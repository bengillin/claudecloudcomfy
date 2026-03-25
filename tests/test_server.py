"""Tests for the Comfy Cloud MCP server — no API calls needed."""

import json
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

from mcp_server.config import COMFY_SH, PRESETS_DIR, DOWNLOADS_DIR, PROJECTS_DIR
from mcp_server.server import (
    _load_preset,
    _list_preset_names,
    _parse_saved_files,
    _run_comfy,
    _classify_error,
    _error_json,
    ComfyError,
    mcp,
)


# ── Config ──────────────────────────────────────────────────────────────


def test_comfy_sh_path_exists():
    assert COMFY_SH.exists(), f"comfy.sh not found at {COMFY_SH}"


def test_comfy_sh_is_executable():
    assert COMFY_SH.stat().st_mode & 0o111, "comfy.sh is not executable"


# ── Preset helpers ──────────────────────────────────────────────────────


def test_list_preset_names_returns_list():
    names = _list_preset_names()
    assert isinstance(names, list)
    # We know there are 5 verified presets
    if PRESETS_DIR.exists():
        assert len(names) >= 1


def test_list_preset_names_excludes_workflows():
    names = _list_preset_names()
    for name in names:
        assert not name.endswith("_workflow"), f"{name} looks like a workflow file"


def test_load_preset_valid():
    names = _list_preset_names()
    if not names:
        pytest.skip("No presets available")
    preset = _load_preset(names[0])
    assert isinstance(preset, dict)
    assert "workflow" in preset, "Preset should have a workflow field"


def test_load_preset_missing():
    with pytest.raises(FileNotFoundError):
        _load_preset("nonexistent_preset_xyz")


# ── Output parsing ──────────────────────────────────────────────────────


def test_parse_saved_files_with_files():
    output = "Submitting...\nPolling...\nSaved downloads/out_00001_.png\nDone"
    result = json.loads(_parse_saved_files(output))
    assert result["status"] == "success"
    assert len(result["files"]) == 1
    assert result["file"].endswith("out_00001_.png")


def test_parse_saved_files_no_files():
    output = "Submitting...\nPolling...\nDone"
    result = json.loads(_parse_saved_files(output))
    assert result["status"] == "success"
    assert "files" not in result


def test_parse_saved_files_multiple():
    output = "Saved downloads/a.png\nSaved downloads/b.png\nSaved downloads/c.mp4"
    result = json.loads(_parse_saved_files(output))
    assert len(result["files"]) == 3
    assert result["file"].endswith("c.mp4")  # last file


# ── _run_comfy ──────────────────────────────────────────────────────────


@pytest.mark.skipif(not os.environ.get("COMFY_API_KEY"), reason="No API key")
def test_run_comfy_help():
    """comfy.sh help should always work without API key."""
    output = _run_comfy("help")
    assert "comfy.sh" in output.lower() or "usage" in output.lower()


def test_run_comfy_failure():
    with pytest.raises(ComfyError, match="failed"):
        _run_comfy("totally-bogus-command-that-does-not-exist")


# ── MCP registration ────────────────────────────────────────────────────


def test_mcp_has_tools():
    tools = mcp._tool_manager._tools
    assert len(tools) == 27


def test_mcp_expected_tools_registered():
    tools = set(mcp._tool_manager._tools.keys())
    expected = {
        "comfy_generate",
        "comfy_animate",
        "comfy_submit",
        "comfy_batch_seed",
        "comfy_list_presets",
        "comfy_upload_image",
        "comfy_asset_search",
        "comfy_job_list",
        "comfy_job_status",
        "comfy_job_wait",
        "comfy_cancel_jobs",
        "comfy_download",
        "comfy_run_workflow",
        "comfy_list_outputs",
        "comfy_project_create",
        "comfy_project_list",
        "comfy_project_log",
        "comfy_project_status",
        "comfy_mv_add_element",
        "comfy_mv_generate_element",
        "comfy_mv_list_elements",
        "comfy_mv_update_element",
        "comfy_mv_plan",
        "comfy_mv_set_prompts",
        "comfy_mv_generate",
        "comfy_mv_stitch",
        "comfy_mv_status",
    }
    assert expected == tools


def test_mcp_has_resources():
    resources = mcp._resource_manager._resources
    assert len(resources) >= 1


def test_mcp_has_prompts():
    prompts = mcp._prompt_manager._prompts
    assert len(prompts) == 2
    assert "creative_brief" in prompts
    assert "evaluate_generation" in prompts


# ── Tool functions (mocked subprocess) ──────────────────────────────────


@patch("mcp_server.server._run_comfy")
def test_comfy_list_presets_returns_json(mock_run):
    from mcp_server.server import comfy_list_presets
    result = comfy_list_presets()
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


@patch("mcp_server.server._run_comfy")
def test_comfy_generate_builds_args(mock_run):
    mock_run.return_value = "Saved downloads/test.png"
    from mcp_server.server import comfy_generate
    result = comfy_generate(preset="z-turbo", prompt="a cat", seed=42)
    call_args = mock_run.call_args[0]
    assert "gen" in call_args
    assert "--preset=z-turbo" in call_args
    assert "--seed=42" in call_args


@patch("mcp_server.server._run_comfy")
def test_comfy_animate_builds_args(mock_run):
    mock_run.return_value = "Saved downloads/test.mp4"
    from mcp_server.server import comfy_animate
    result = comfy_animate(image_path="/tmp/img.png", preset="wan22-i2v", prompt="zoom in")
    call_args = mock_run.call_args[0]
    assert "animate" in call_args
    assert "/tmp/img.png" in call_args
    assert "--preset=wan22-i2v" in call_args


def test_comfy_list_outputs_empty(tmp_path):
    """list_outputs handles missing downloads dir gracefully."""
    from mcp_server.server import comfy_list_outputs
    with patch("mcp_server.server.DOWNLOADS_DIR", tmp_path / "nonexistent"):
        result = json.loads(comfy_list_outputs())
        assert result["files"] == []


def test_comfy_list_outputs_with_files(tmp_path):
    from mcp_server.server import comfy_list_outputs
    (tmp_path / "a.png").write_text("fake")
    (tmp_path / "b.mp4").write_text("fake")
    with patch("mcp_server.server.DOWNLOADS_DIR", tmp_path):
        result = json.loads(comfy_list_outputs())
        assert len(result["files"]) == 2

        # filter by extension
        result = json.loads(comfy_list_outputs(extension="png"))
        assert len(result["files"]) == 1
        assert result["files"][0]["name"] == "a.png"


# ── Project tools (temp directory) ──────────────────────────────────────


def test_project_lifecycle(tmp_path):
    from mcp_server.server import (
        comfy_project_create,
        comfy_project_list,
        comfy_project_log,
        comfy_project_status,
    )

    with patch("mcp_server.server.PROJECTS_DIR", tmp_path):
        # Create
        result = json.loads(comfy_project_create(name="test-proj", brief="A test project"))
        assert result["status"] == "created"

        # Duplicate
        result = json.loads(comfy_project_create(name="test-proj", brief="dup"))
        assert "error" in result

        # List
        result = json.loads(comfy_project_list())
        assert len(result["projects"]) == 1
        assert result["projects"][0]["name"] == "test-proj"

        # Log
        result = json.loads(comfy_project_log(
            name="test-proj",
            action="generate",
            details="Test generation",
            preset="z-turbo",
            prompt="a cat",
            seed=42,
        ))
        assert result["status"] == "logged"

        # Status
        result = json.loads(comfy_project_status(name="test-proj"))
        assert len(result["log"]) == 1
        assert result["log"][0]["seed"] == 42

        # Not found
        result = json.loads(comfy_project_status(name="nope"))
        assert "error" in result


# ── Error classification ─────────────────────────────────────────────


def test_classify_error_auth():
    cat, hint = _classify_error("401 Unauthorized", "")
    assert cat == "auth"


def test_classify_error_rate_limit():
    cat, hint = _classify_error("429 Too Many Requests", "")
    assert cat == "rate_limit"


def test_classify_error_quota():
    cat, hint = _classify_error("402 insufficient credits", "")
    assert cat == "quota"


def test_classify_error_not_found():
    cat, hint = _classify_error("404 Not Found", "")
    assert cat == "not_found"


def test_classify_error_unknown():
    cat, hint = _classify_error("something weird happened", "")
    assert cat == "unknown"


def test_error_json_format():
    e = ComfyError("test error", category="rate_limit", hint="wait and retry")
    result = json.loads(_error_json(e))
    assert result["status"] == "error"
    assert result["category"] == "rate_limit"
    assert result["hint"] == "wait and retry"
    assert "test error" in result["error"]


def test_handle_errors_catches_comfy_error():
    """Tools with @_handle_errors return JSON instead of raising."""
    from mcp_server.server import comfy_generate
    with patch("mcp_server.server._run_comfy", side_effect=ComfyError("boom", "auth", "check key")):
        result = json.loads(comfy_generate(preset="z-turbo", prompt="test"))
        assert result["status"] == "error"
        assert result["category"] == "auth"


# ── Async submit ─────────────────────────────────────────────────────


@patch("mcp_server.server._run_comfy")
@patch("mcp_server.server._load_preset")
def test_comfy_submit_returns_job_id(mock_preset, mock_run):
    mock_preset.return_value = {
        "workflow": "z-turbo_workflow.json",
        "prompt_node": "6",
        "prompt_field": "text",
        "seed_node": "3",
    }
    mock_run.return_value = '{"prompt_id": "abc-123"}'
    from mcp_server.server import comfy_submit
    result = json.loads(comfy_submit(preset="z-turbo", prompt="a cat", seed=42))
    assert result["status"] == "submitted"
    assert result["job_id"] == "abc-123"
    # Verify it used run-with (submit-only, no poll)
    call_args = mock_run.call_args[0]
    assert "run-with" in call_args
