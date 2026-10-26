# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Discover the user's N.E.K.O configuration so the pack reuses their real voice.

The plugin runs in its own process, so it cannot ask the host for a resolved
config through the SDK. It therefore reads the same files the host reads, in the
same precedence order the host uses, and degrades to a documented default when a
file is missing rather than failing the whole build.

Nothing here writes to the user's configuration.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The free tier's shared credential. It is not a secret (it ships in
# config/api_providers.json as "audio_api_key": "free-access") and the service
# only serves the free preset voices plus the account's own cloned voices.
FREE_ACCESS_KEY = "free-access"

# Fallback voice when the user has never picked one: the free preset the host
# itself defaults to (config/api_providers.json -> free_voices.yui_cn).
FALLBACK_FREE_VOICE_KEY = "yui_cn"


def _env_paths() -> list[Path]:
    out: list[Path] = []
    explicit = os.environ.get("NEKO_USER_DATA_DIR") or os.environ.get("NEKO_USER_DATA")
    if explicit:
        out.append(Path(explicit))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "N.E.K.O")
    roaming = os.environ.get("APPDATA")
    if roaming:
        out.append(Path(roaming) / "N.E.K.O")
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    out.append(Path(home) / "Documents" / "N.E.K.O")
    return out


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from this file looking for the N.E.K.O checkout root."""
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "config" / "api_providers.json").is_file():
            return candidate
    return None


def find_user_config_dir() -> Path | None:
    """First existing ``<user data>/config`` directory."""
    for root in _env_paths():
        candidate = root / "config"
        if candidate.is_dir():
            return candidate
    return None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class NekoSettings:
    """Resolved TTS-relevant settings, plus where each value came from."""

    voice_id: str
    voice_source: str
    url: str
    url_source: str
    api_key: str
    key_source: str
    free_voices: dict[str, str]
    core_config_path: str
    repo_root: str

    def describe(self) -> str:
        return (
            f"voice={self.voice_id or '<none>'} ({self.voice_source}), "
            f"url={self.url} ({self.url_source}), "
            f"key={'set' if self.api_key else 'empty'} ({self.key_source}), "
            f"free_presets={len(self.free_voices)}"
        )


def load_settings(*, voice_override: str = "", url_override: str = "",
                  key_override: str = "", region: str = "cn") -> NekoSettings:
    """Resolve voice/url/key with an explicit precedence chain.

    Order, most specific first:
      1. explicit argument (plugin config / entry parameter)
      2. the user's ``core_config.json``
      3. the free preset default from the checkout's ``api_providers.json``

    Region only decides the *default* free endpoint; an explicit URL always wins.
    """
    user_dir = find_user_config_dir()
    core_path = (user_dir / "core_config.json") if user_dir else None
    core = _read_json(core_path)

    repo_root = find_repo_root()
    providers = _read_json((repo_root / "config" / "api_providers.json") if repo_root else None)
    free_voices = {
        str(k): str(v) for k, v in (providers.get("free_voices") or {}).items()
        if str(v).strip()
    }

    voice_id, voice_source = "", "none"
    for candidate, source in (
        (voice_override.strip(), "plugin config"),
        (str(core.get("ttsVoiceId") or "").strip(), "core_config.ttsVoiceId"),
        (free_voices.get(FALLBACK_FREE_VOICE_KEY, "").strip(),
         f"api_providers.free_voices.{FALLBACK_FREE_VOICE_KEY}"),
    ):
        if candidate:
            voice_id, voice_source = candidate, source
            break

    default_url = ("wss://www.lanlan.app/tts" if str(region).lower() in ("intl", "oversea", "global")
                   else "wss://www.lanlan.tech/tts")
    url, url_source = default_url, f"default ({region})"
    for candidate, source in (
        (url_override.strip(), "plugin config"),
        (str(os.environ.get("NEKO_STELLARIS_TTS_URL") or "").strip(), "env NEKO_STELLARIS_TTS_URL"),
    ):
        if candidate:
            url, url_source = candidate, source
            break

    api_key, key_source = "", "none"
    for candidate, source in (
        (key_override.strip(), "plugin config"),
        (str(core.get("ttsModelApiKey") or "").strip(), "core_config.ttsModelApiKey"),
        (str(core.get("coreApiKey") or "").strip(), "core_config.coreApiKey"),
        (FREE_ACCESS_KEY, "free-access default"),
    ):
        if candidate:
            api_key, key_source = candidate, source
            break

    return NekoSettings(
        voice_id=voice_id,
        voice_source=voice_source,
        url=url,
        url_source=url_source,
        api_key=api_key,
        key_source=key_source,
        free_voices=free_voices,
        core_config_path=str(core_path) if core_path else "",
        repo_root=str(repo_root) if repo_root else "",
    )


def list_voices() -> list[dict[str, str]]:
    """Free preset voices the service exposes, for the plugin's UI/status."""
    repo_root = find_repo_root()
    providers = _read_json((repo_root / "config" / "api_providers.json") if repo_root else None)
    return [
        {"key": str(k), "voice_id": str(v), "source": "free_preset"}
        for k, v in (providers.get("free_voices") or {}).items()
    ]


def find_stellaris_user_dir() -> Path | None:
    """``Documents/Paradox Interactive/Stellaris`` (mod + log + save root)."""
    explicit = os.environ.get("NEKO_STELLARIS_USER_DIR")
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    for candidate in (
        Path(home) / "Documents" / "Paradox Interactive" / "Stellaris",
        Path(home) / "OneDrive" / "Documents" / "Paradox Interactive" / "Stellaris",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _host_runtime_root() -> Path | None:
    """The runtime data root the SDK hands the plugin, computed without the SDK.

    The CLI runs where the SDK is not importable. Both fallbacks below used to
    invent ``~/.neko_stellaris`` instead, which is somewhere the host never looks:
    the plugin cached 248 clips under the real root, the CLI saw an empty
    directory and re-synthesized all of them against a rate-limited endpoint. The
    host's own choice is per platform.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "N.E.K.O" if base else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "N.E.K.O"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "N.E.K.O"


def _clip_count(directory: Path) -> int:
    try:
        return sum(1 for _ in directory.glob("*.wav"))
    except OSError:
        return 0


def tts_cache_dir() -> Path:
    """Where synthesized clips are cached, shared by the plugin and the CLI.

    The SDK resolves ``ctx.cache_path("tts")`` to
    ``<runtime data root>/plugins/<id>/cache/tts``. The CLI previously invented
    its own ``data/tts_cache`` beside the build output, so the two never shared a
    cache: a rebuild from the terminal re-synthesized all 248 clips and hit the
    free endpoint's rate limiter from scratch.

    Without the SDK the same mistake is still available in a subtler form -- guess
    the wrong root and the cache looks empty -- so candidates are ranked by how
    many clips they already hold rather than by preference. Picking an empty
    directory is silent and expensive; picking a full one is free and correct.
    """
    try:
        from plugin.sdk.shared.core.base_runtime import resolve_runtime_data_root
        return resolve_runtime_data_root() / "plugins" / "neko_stellaris" / "cache" / "tts"
    except Exception:
        pass

    candidates: list[Path] = []
    root = _host_runtime_root()
    if root is not None:
        candidates.append(root / "plugins" / "neko_stellaris" / "cache" / "tts")
    candidates.append(Path.home() / ".neko_stellaris" / "cache" / "tts")
    return max(candidates, key=_clip_count)


def default_work_dir() -> Path:
    """Scratch/build output root, deliberately *outside* the plugin directory.

    A ``.build/`` next to the sources would be picked up by the packaging rules
    (only ``dist`` and ``build`` are excluded, not ``.build``) and ship the
    generated wavs inside the plugin.
    """
    try:
        from plugin.sdk.shared.core.base_runtime import resolve_runtime_data_root
        return resolve_runtime_data_root() / "plugins" / "neko_stellaris" / "data"
    except Exception:
        root = _host_runtime_root()
        if root is not None:
            return root / "plugins" / "neko_stellaris" / "data"
        return Path.home() / ".neko_stellaris" / "data"


def find_stellaris_install_dir() -> Path | None:
    """Stellaris install root, used to re-extract advisor sound names after a patch."""
    explicit = os.environ.get("NEKO_STELLARIS_INSTALL_DIR")
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    steam_roots = [
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
    ]
    candidates: list[Path] = []
    for root in steam_roots:
        if root:
            candidates.append(Path(root) / "Steam" / "steamapps" / "common" / "Stellaris")
    # Steam library folders from the registry are not readable from a plugin
    # sandbox in every setup, so also probe the common explicit drives.
    for drive in ("C:", "D:", "E:", "F:"):
        candidates.append(Path(f"{drive}/Steam/steamapps/common/Stellaris"))
        candidates.append(Path(f"{drive}/SteamLibrary/steamapps/common/Stellaris"))
        candidates.append(Path(f"{drive}/Games/Steam/steamapps/common/Stellaris"))
    for candidate in candidates:
        if (candidate / "stellaris.exe").is_file() or (candidate / "sound").is_dir():
            return candidate
    return None
