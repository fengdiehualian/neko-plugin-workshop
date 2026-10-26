"""Explicit optional website-account adapter; never owns a game connection."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PORTAL = "https://mc.kangqiang.site"
ADDRESS = "micro.kangqiang.site:25702"
VERSION = "1.21.1"
HELPER = Path(__file__).parent / "assets/server-extensions/qiandengji/access/agent-register.mjs"
HELPER_SHA256 = "cfa9733c093a27093100b3c08b21de0727af24f486561a07bf5a7868d49ca5cd"
NAME = re.compile(r"ag_[A-Za-z0-9_]{1,13}\Z")
STATES = {"ready", "pending", "unknown", "not_created", "awaiting_game_bridge", "authenticated", "registered", "revoked", "expired"}


class AccountError(ValueError):
    """Only locally defined codes cross the host boundary, never API text."""


def private_path(value, *, missing=False):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise AccountError("private_absolute_file_required")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise AccountError("private_absolute_file_required")
    path = path.resolve()
    # Keep user-created secrets out of the distributable source tree.
    if path.is_relative_to(Path(__file__).parent.resolve()):
        raise AccountError("identity_outside_plugin_required")
    if not path.exists() and missing:
        return path
    try:
        stat = path.stat()
        if not path.is_file() or not 0 < stat.st_size <= 16384 or (os.name != "nt" and stat.st_mode & 0o077):
            raise AccountError("private_file_unavailable")
    except OSError:
        raise AccountError("private_file_unavailable") from None
    return path


def configured_identity(raw):
    section = raw.get("qiandengji", {})
    if not isinstance(section, dict) or set(section) - {"identity_file"}:
        raise AccountError("invalid_qiandengji_settings")
    value = section.get("identity_file", "")
    if value:
        private_path(value, missing=True)
    elif value != "":
        raise AccountError("private_absolute_file_required")
    return value


def read_identity(value):
    path = private_path(value)
    try:
        data = json.loads(path.read_text("utf-8-sig"))
        if not isinstance(data, dict) or data.get("schemaVersion") != 1 or data.get("portal") != PORTAL:
            raise ValueError()
        if not NAME.fullmatch(data.get("username", "")) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", data.get("clientSecret", "")):
            raise ValueError()
        for key in ("clientId", "gameRequestId"):
            if str(uuid.UUID(data[key])) != data[key].lower():
                raise ValueError()
        password = data.get("gamePassword")
        if not isinstance(password, str) or not 6 <= len(password) <= 64 or re.search(r"\s|[\x00-\x1f\x7f]", password):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise AccountError("invalid_private_identity") from None
    return path, data


def public_result(result):
    """Do not forward API messages, nested data, arbitrary strings or credentials."""
    state = result.get("state") if isinstance(result, dict) else None
    state = state if state in STATES else "unknown"
    username = result.get("username") if isinstance(result, dict) else None
    username = username if isinstance(username, str) and NAME.fullmatch(username) else None
    created = isinstance(result, dict) and result.get("gameAccountCreated") is True
    return {"state": state, "username": username, "game_account_created": created,
            "ready": state == "ready" and created, "game_address": ADDRESS,
            "game_version": VERSION, "game_connection_started": False}


def require_ready_result(result, identity):
    if (not isinstance(result, dict) or result.get("state") != "ready" or result.get("gameAccountCreated") is not True
            or result.get("username") != identity["username"] or result.get("gameAddress") != ADDRESS
            or result.get("gameVersion") != VERSION):
        raise AccountError("account_not_ready_query_status")


async def _invoke(args, *, node="node"):
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA256:
        raise AccountError("official_helper_integrity_failed")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            node, str(HELPER), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        out, err = await asyncio.wait_for(process.communicate(), timeout=70)
        if len(out) > 65536 or len(err) > 65536:
            raise AccountError("account_result_unknown_query_status")
        if process.returncode not in {0, 2}:
            raise AccountError("account_result_unknown_query_status")
        value = json.loads(out.decode("utf-8").strip())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, UnicodeError, ValueError, asyncio.TimeoutError) as error:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        if isinstance(error, AccountError):
            raise
        raise AccountError("account_result_unknown_query_status") from None
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        raise


async def account_action(action, identity_file, *, username="", invite_url_file="", node="node"):
    if action not in {"register", "status", "activate"}:
        raise AccountError("invalid_account_action")
    if action == "register":
        path = private_path(identity_file, missing=True)
        if path.exists():
            raise AccountError("identity_exists_query_status")
        if not isinstance(username, str) or not NAME.fullmatch(username):
            raise AccountError("invalid_agent_name")
        invitation = private_path(invite_url_file)
        try:
            url = urlsplit(invitation.read_text("utf-8-sig").strip())
            params = parse_qs(url.fragment, strict_parsing=True)
            if (url.scheme != "https" or url.netloc != "mc.kangqiang.site" or url.path != "/join" or url.query
                    or set(params) != {"invite"} or len(params["invite"]) != 1
                    or not re.fullmatch(r"qdi_[A-Za-z0-9_-]{43}", params["invite"][0])):
                raise ValueError()
        except (OSError, ValueError):
            raise AccountError("invalid_private_invitation") from None
        args = [action, "--username", username, "--invite-url-file", str(invitation), "--identity-file", str(path)]
    else:
        path, _ = read_identity(identity_file)
        args = [action, "--identity-file", str(path)]
    result = await _invoke(args, node=node)
    _, identity = read_identity(str(path))
    if result.get("username") != identity["username"]:
        raise AccountError("account_response_mismatch_query_status")
    summary = public_result(result)
    summary["ready"] = (summary["ready"] and result.get("gameAddress") == ADDRESS and result.get("gameVersion") == VERSION)
    return summary


def _private_write_same(path, contents):
    if path.exists():
        private_path(str(path))
        if path.read_bytes() != contents:
            raise AccountError("private_material_conflict")
        return
    try:
        with path.open("xb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(contents)
    except FileExistsError:
        if path.read_bytes() != contents:
            raise AccountError("private_material_conflict") from None


def login_material(identity):
    return {"enabled": True, "accounts": [{"server": ADDRESS, "username": identity["username"],
            "credentialFile": "game-password.private", "credentialFormat": "file", "command": "login"}]}


def prepare_login(source, identity, directory):
    # No alternate password, registration command or controller is introduced.
    _, original = read_identity(str(source))
    if any(original[key] != identity[key] for key in ("username", "clientId", "gameRequestId", "gamePassword")):
        raise AccountError("private_identity_changed")
    directory = Path(directory) / "qiandengji" / identity["clientId"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_path(str(directory / "game-password.private"), missing=True)
    secret = (identity["gamePassword"] + "\n").encode("utf-8")
    config = (json.dumps(login_material(identity), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _private_write_same(directory / "game-password.private", secret)
    _private_write_same(directory / "login.json", config)
    return directory / "login.json"


async def preflight(raw, settings):
    value = configured_identity(raw)
    if not value:
        return
    _, identity = read_identity(value)
    if (settings.host != "micro.kangqiang.site" or settings.port != 25702 or settings.username != identity["username"]
            or settings.auth != "offline" or settings.version != VERSION):
        raise AccountError("imported_account_connection_mismatch")
    login = private_path(settings.login_config_file)
    try:
        config = json.loads(login.read_text("utf-8"))
        if config != login_material(identity):
            raise ValueError()
        secret = private_path(str(login.parent / "game-password.private"))
        if secret.read_text("utf-8") != identity["gamePassword"] + "\n":
            raise ValueError()
    except (OSError, ValueError):
        raise AccountError("private_login_mismatch") from None
    result = await _invoke(["status", "--identity-file", str(Path(value))], node=settings.node)
    require_ready_result(result, identity)
    _, latest = read_identity(value)
    if any(latest[key] != identity[key] for key in ("username", "clientId", "gameRequestId", "gamePassword")):
        raise AccountError("private_identity_changed")
