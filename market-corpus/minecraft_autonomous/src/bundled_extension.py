"""Select a byte-checked optional resource pack; server logic lives upstream."""

import hashlib
import json
from pathlib import Path

PACK = Path(__file__).resolve().parent / "assets" / "server-extensions" / "qiandengji"


def extension_mode(raw):
    section = raw.get("extension_pack", {})
    if not isinstance(section, dict) or set(section) - {"mode"} or section.get("mode", "auto") not in {"auto", "none", "custom"}:
        raise ValueError("extension_pack.mode must be auto, none or custom")
    return section.get("mode", "auto")


def bundled_extension_status(raw):
    mode = extension_mode(raw)
    try:
        source = PACK / "source.json"
        if source.stat().st_size > 16384:
            raise ValueError("pack_metadata_too_large")
        metadata = json.loads(source.read_text(encoding="utf-8"))
        module = PACK / "presentation.mjs"
        if (metadata.get("schema") != "agent-mc-server-presentation/v1" or metadata.get("minecraft_version") != "1.21.1"
                or module.stat().st_size > 262144 or hashlib.sha256(module.read_bytes()).hexdigest() != metadata.get("module_sha256")):
            raise ValueError("pack_integrity_mismatch")
        resources = metadata.get("resources", {})
        if not isinstance(resources, dict) or len(resources) > 64:
            raise ValueError("pack_resource_manifest_invalid")
        for relative, expected in resources.items():
            if (not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute()
                    or any(part in {"", ".", ".."} for part in relative.split("/")) or not isinstance(expected, dict)):
                raise ValueError("pack_resource_path_invalid")
            resource = (PACK / relative).resolve()
            if (not resource.is_relative_to(PACK.resolve()) or not resource.is_file()
                    or resource.stat().st_size > 1048576 or resource.stat().st_size != expected.get("bytes")
                    or hashlib.sha256(resource.read_bytes()).hexdigest() != expected.get("sha256")):
                raise ValueError("pack_resource_integrity_mismatch")
        return {"mode": mode, "available": True, "id": "qiandengji", "version": metadata.get("version"),
                "module": str(module), "sha256": metadata["module_sha256"], "activation": "validated_same_player_server_data"}
    except (OSError, ValueError, TypeError):
        return {"mode": mode, "available": False, "reason": "optional_pack_missing_or_invalid"}


def default_bundled_extension_settings(raw):
    mode = extension_mode(raw)
    if mode == "none":
        return {**raw, "server_extension": {"presentation_module": "", "presentation_sha256": ""}}
    explicit = raw.get("server_extension", {})
    if not isinstance(explicit, dict):
        raise ValueError("server_extension must be a table")
    if mode == "custom" or explicit.get("presentation_module"):
        return raw
    status = bundled_extension_status(raw)
    if not status["available"]:
        return raw
    return {**raw, "server_extension": {"presentation_module": status["module"], "presentation_sha256": status["sha256"]}}
