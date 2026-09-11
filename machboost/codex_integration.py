from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .relay import relay_home, relay_status, start_gateway_relay, stop_claude_gateway_relay


CODEX_PROFILE_NAME = "machboost-launch"
CODEX_PROVIDER_NAME = "MachBoost"
CHATGPT_STATE_SCHEMA = "machboost.chatgpt-profile-state.v1"
CODEX_CLI_STATE_SCHEMA = "machboost.codex-cli-profile-state.v1"
CHATGPT_BUNDLE_ID = "com.openai.codex"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def chatgpt_relay_state_path() -> Path:
    return relay_home() / "chatgpt-loopback-relay.json"


def chatgpt_state_path() -> Path:
    return relay_home() / "chatgpt-profile-state.json"


def codex_cli_state_path() -> Path:
    return relay_home() / "codex-cli-profile-state.json"


def chatgpt_catalog_path(home: Optional[Path] = None) -> Path:
    return Path(home or codex_home()) / "machboost-chatgpt-models.json"


def codex_cli_catalog_path(home: Optional[Path] = None) -> Path:
    return Path(home or codex_home()) / "machboost-models.json"


def codex_cli_profile_path(home: Optional[Path] = None) -> Path:
    return Path(home or codex_home()) / f"{CODEX_PROFILE_NAME}.config.toml"


def runnable_model_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        row = dict(raw)
        name = str(row.get("name") or row.get("id") or "").strip()
        if not name or name.startswith("claude-") or name in seen:
            continue
        support = str(row.get("support") or "ready").strip().lower()
        if support in {"unsupported", "missing_runtime", "error"}:
            continue
        if row.get("cached") is False:
            continue
        row["name"] = name
        seen.add(name)
        result.append(row)
    return result


def select_model_rows(
    rows: Iterable[dict[str, Any]],
    selected: Sequence[str] = (),
) -> list[dict[str, Any]]:
    available = runnable_model_rows(rows)
    if not selected:
        return available
    by_name: dict[str, dict[str, Any]] = {}
    for row in available:
        for value in (row.get("name"), row.get("repository"), row.get("source_repository")):
            if value:
                by_name[str(value)] = row
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in selected:
        name = str(value).strip()
        if not name or name in seen:
            continue
        row = dict(by_name.get(name) or {"name": name, "capabilities": ["chat", "tools"]})
        row["name"] = name
        result.append(row)
        seen.add(name)
    return result


def codex_model_catalog(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    models = [_codex_model_entry(row, -index) for index, row in enumerate(rows, start=1)]
    if not models:
        raise ValueError("no downloaded MachBoost models are available for this integration")
    return {"models": models}


def _codex_model_entry(row: dict[str, Any], priority: int) -> dict[str, Any]:
    name = str(row["name"])
    capabilities = {str(item).lower() for item in row.get("capabilities") or ()}
    reasoning = "reasoning" in capabilities or "thinking" in capabilities
    tool_capable = "tools" in capabilities or "tool_calls" in capabilities
    context_window = int(row.get("context_length") or 32_768)
    context_window = max(4_096, context_window)
    input_modalities = ["text", "image"] if "vision" in capabilities else ["text"]
    levels = (
        [
            {"effort": "none", "description": "Answer without extended reasoning"},
            {"effort": "medium", "description": "Use the model's native reasoning mode"},
        ]
        if reasoning
        else []
    )
    return {
        "slug": name,
        "display_name": str(row.get("display_name") or name),
        "description": "Local or shared model served by MachBoost",
        "default_reasoning_level": "medium" if reasoning else None,
        "supported_reasoning_levels": levels,
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": None,
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": "",
        "model_messages": None,
        "include_skills_usage_instructions": True,
        "include_plugin_usage_instructions": True,
        "include_apps_usage_instructions": True,
        "supports_reasoning_summary_parameter": False,
        "supports_reasoning_summaries": False,
        "default_reasoning_summary": "auto",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": None,
        "web_search_tool_type": "text" if tool_capable else None,
        "truncation_policy": {"mode": "tokens", "limit": min(10_000, context_window // 2)},
        "supports_parallel_tool_calls": tool_capable,
        "supports_image_detail_original": False,
        "context_window": context_window,
        "max_context_window": context_window,
        "auto_compact_token_limit": int(context_window * 0.9),
        "effective_context_window_percent": 90,
        "experimental_supported_tools": [],
        "input_modalities": input_modalities,
        "supports_search_tool": tool_capable,
    }


class CodexCLIProfileManager:
    def __init__(
        self,
        *,
        home: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self.home = Path(home or codex_home())
        self.profile_path = codex_cli_profile_path(self.home)
        self.catalog_path = codex_cli_catalog_path(self.home)
        self.state_path = Path(state_path or codex_cli_state_path())

    def configure(
        self,
        endpoint: str,
        api_key: str,
        rows: Iterable[dict[str, Any]],
        *,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        endpoint = _normalize_openai_base_url(endpoint)
        api_key = str(api_key).strip()
        if not api_key:
            raise ValueError("MachBoost API key is required")
        selected_rows = list(rows)
        if not selected_rows:
            raise ValueError("select at least one downloaded model")
        primary = str(model or selected_rows[0]["name"]).strip()
        self._capture_state_once()
        _write_json(self.catalog_path, codex_model_catalog(selected_rows))
        profile = (
            f'model = {json.dumps(primary)}\n'
            f'model_provider = {json.dumps(CODEX_PROFILE_NAME)}\n'
            f'model_catalog_json = {json.dumps(str(self.catalog_path))}\n\n'
            f'[model_providers.{CODEX_PROFILE_NAME}]\n'
            f'name = {json.dumps(CODEX_PROVIDER_NAME)}\n'
            f'base_url = {json.dumps(endpoint)}\n'
            'env_key = "MACHBOOST_API_TOKEN"\n'
            'wire_api = "responses"\n'
            'stream_idle_timeout_ms = 300000\n'
        )
        _validate_toml(profile)
        _write_text(self.profile_path, profile)
        return self.status()

    def status(self) -> dict[str, Any]:
        config = _read_toml(self.profile_path)
        provider = dict((config.get("model_providers") or {}).get(CODEX_PROFILE_NAME) or {})
        return {
            "schema": "machboost.codex-cli-status.v1",
            "configured": config.get("model_provider") == CODEX_PROFILE_NAME,
            "installed": codex_executable() is not None,
            "model": config.get("model"),
            "endpoint": provider.get("base_url"),
            "profile": CODEX_PROFILE_NAME,
        }

    def restore(self) -> dict[str, Any]:
        state = _read_json(self.state_path)
        for path, key in ((self.profile_path, "profile"), (self.catalog_path, "catalog")):
            snapshot = state.get(key) if state.get("schema") == CODEX_CLI_STATE_SCHEMA else None
            _restore_file(path, snapshot)
        _remove_file(self.state_path)
        return self.status()

    def command(self, model: Optional[str] = None, extra: Sequence[str] = ()) -> list[str]:
        executable = codex_executable()
        if executable is None:
            raise RuntimeError("Codex CLI is not installed and the ChatGPT app has no bundled Codex executable")
        command = [str(executable), "--profile", CODEX_PROFILE_NAME]
        if model:
            command.extend(["-m", str(model)])
        command.extend(str(item) for item in extra)
        return command

    def run(
        self,
        api_key: str,
        *,
        model: Optional[str] = None,
        extra: Sequence[str] = (),
    ) -> int:
        environment = dict(os.environ)
        environment["MACHBOOST_API_TOKEN"] = str(api_key)
        return subprocess.run(self.command(model, extra), env=environment, check=False).returncode

    def _capture_state_once(self) -> None:
        if self.state_path.exists():
            return
        _write_json(
            self.state_path,
            {
                "schema": CODEX_CLI_STATE_SCHEMA,
                "profile": _snapshot_file(self.profile_path),
                "catalog": _snapshot_file(self.catalog_path),
            },
        )


class ChatGPTProfileManager:
    managed_keys = ("profile", "model", "model_provider", "model_catalog_json", "openai_base_url")

    def __init__(
        self,
        *,
        home: Optional[Path] = None,
        state_path: Optional[Path] = None,
        relay_state_path: Optional[Path] = None,
    ) -> None:
        self.home = Path(home or codex_home())
        self.config_path = self.home / "config.toml"
        self.catalog_path = chatgpt_catalog_path(self.home)
        self.state_path = Path(state_path or chatgpt_state_path())
        self.relay_state_path = Path(relay_state_path or chatgpt_relay_state_path())

    def configure(
        self,
        endpoint: str,
        rows: Iterable[dict[str, Any]],
        *,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        if platform.system() != "Darwin":
            raise RuntimeError("ChatGPT desktop integration is currently supported on macOS")
        selected_rows = list(rows)
        if not selected_rows:
            raise ValueError("select at least one downloaded model")
        primary = str(model or selected_rows[0]["name"]).strip()
        base_url = _normalize_openai_base_url(endpoint)
        text = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        parsed = _validate_toml(text)
        self._capture_state_once(parsed)
        for key in ("profile", "model_provider"):
            text = _remove_root_assignment(text, key)
        for key, value in (
            ("model", primary),
            ("model_catalog_json", str(self.catalog_path)),
            ("openai_base_url", base_url),
        ):
            text = _set_root_string(text, key, value)
        _validate_toml(text)
        _write_json(self.catalog_path, codex_model_catalog(selected_rows))
        _write_text(self.config_path, text)
        return self.status()

    def status(self) -> dict[str, Any]:
        config = _read_toml(self.config_path)
        relay = relay_status(self.relay_state_path)
        configured = config.get("model_catalog_json") == str(self.catalog_path) and bool(
            config.get("openai_base_url")
        )
        return {
            "schema": "machboost.chatgpt-status.v1",
            "installed": installed_chatgpt_application() is not None,
            "connected": configured,
            "model": config.get("model") if configured else None,
            "endpoint": config.get("openai_base_url") if configured else None,
            "upstream": relay.get("upstream") if relay.get("running") else None,
            "relayed": bool(relay.get("running")),
        }

    def restore(self) -> dict[str, Any]:
        text = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        state = _read_json(self.state_path)
        values = state.get("values") if state.get("schema") == CHATGPT_STATE_SCHEMA else {}
        for key in self.managed_keys:
            snapshot = values.get(key) if isinstance(values, dict) else None
            if isinstance(snapshot, dict) and snapshot.get("present"):
                text = _set_root_value(text, key, snapshot.get("value"))
            else:
                text = _remove_root_assignment(text, key)
        _validate_toml(text)
        _write_text(self.config_path, text)
        _remove_file(self.catalog_path)
        _remove_file(self.state_path)
        stop_claude_gateway_relay(state_path=self.relay_state_path)
        return self.status()

    def restart_application(self) -> None:
        app = installed_chatgpt_application()
        if app is None:
            raise RuntimeError("ChatGPT is not installed")
        subprocess.run(
            ["/usr/bin/osascript", "-e", 'tell application "ChatGPT" to quit'],
            check=False,
            capture_output=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if subprocess.run(
                ["/usr/bin/pgrep", "-f", "ChatGPT.app/Contents/MacOS/ChatGPT"],
                check=False,
                capture_output=True,
            ).returncode != 0:
                break
            time.sleep(0.2)
        launched = subprocess.run(
            ["/usr/bin/open", str(app), "--args", "codex://threads/new?mode=codex"],
            check=False,
            capture_output=True,
            text=True,
        )
        if launched.returncode != 0:
            launched = subprocess.run(
                ["/usr/bin/open", "-b", CHATGPT_BUNDLE_ID],
                check=False,
                capture_output=True,
                text=True,
            )
        if launched.returncode != 0:
            detail = launched.stderr.strip() or "LaunchServices rejected the request"
            raise RuntimeError(f"ChatGPT was configured but could not reopen: {detail}")

    def _capture_state_once(self, config: dict[str, Any]) -> None:
        if self.state_path.exists():
            return
        values = {
            key: {"present": key in config, "value": config.get(key)}
            for key in self.managed_keys
        }
        _write_json(self.state_path, {"schema": CHATGPT_STATE_SCHEMA, "values": values})


def start_chatgpt_gateway_relay(upstream: str, token: str) -> str:
    endpoint, _ = start_gateway_relay(
        upstream,
        token,
        preferred_port=11437,
        state_path=chatgpt_relay_state_path(),
        accept_any_local_auth=True,
    )
    return endpoint


def installed_chatgpt_application(home: Optional[Path] = None) -> Optional[Path]:
    home = Path(home or Path.home())
    for path in (
        Path("/Applications/ChatGPT.app"),
        Path("/Applications/Codex.app"),
        home / "Applications/ChatGPT.app",
        home / "Applications/Codex.app",
    ):
        if path.exists():
            return path
    return None


def codex_executable(home: Optional[Path] = None) -> Optional[Path]:
    command = shutil.which("codex")
    if command:
        return Path(command)
    app = installed_chatgpt_application(home)
    if app is not None:
        bundled = app / "Contents" / "Resources" / "codex"
        if bundled.is_file():
            return bundled
    return None


def _normalize_openai_base_url(endpoint: str) -> str:
    value = str(endpoint).strip().rstrip("/")
    if not re.match(r"^https?://", value):
        value = "http://" + value
    if value.endswith("/v1"):
        return value
    return value + "/v1"


def _root_end(text: str) -> int:
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            return offset
        offset += len(line)
    return len(text)


def _remove_root_assignment(text: str, key: str) -> str:
    end = _root_end(text)
    root = text[:end]
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=.*(?:\n|$)")
    return pattern.sub("", root) + text[end:]


def _set_root_string(text: str, key: str, value: str) -> str:
    return _set_root_value(text, key, str(value))


def _set_root_value(text: str, key: str, value: Any) -> str:
    text = _remove_root_assignment(text, key)
    end = _root_end(text)
    root = text[:end]
    suffix = text[end:]
    if root and not root.endswith("\n"):
        root += "\n"
    root += f"{key} = {json.dumps(value)}\n"
    if suffix and not root.endswith("\n\n"):
        root += "\n"
    return root + suffix


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return _validate_toml(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return {}


def _validate_toml(text: str) -> dict[str, Any]:
    return tomllib.loads(text) if text.strip() else {}


def _snapshot_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    return {"exists": True, "text": path.read_text(encoding="utf-8")}


def _restore_file(path: Path, snapshot: Any) -> None:
    if isinstance(snapshot, dict) and snapshot.get("exists"):
        _write_text(path, str(snapshot.get("text") or ""))
    else:
        _remove_file(path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, indent=2) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
