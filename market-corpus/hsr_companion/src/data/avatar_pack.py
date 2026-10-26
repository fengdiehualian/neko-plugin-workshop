"""Small, separately cached upstream portrait templates; no game automation.

Source mappings and Git blob hashes are pinned in resources/avatar_templates.json.
OpenCV provides the SIFT/RANSAC implementation; this module only adapts its
results to the plugin's strict character IDs and bounded HUD regions.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1
from io import BytesIO
from pathlib import Path
from typing import Any


def git_blob_hash(data: bytes) -> str:
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "NEKO-hsr-portrait-pack/0.9"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read(2 * 1024 * 1024 + 1)
            break
        except (TimeoutError, urllib.error.URLError):
            if attempt:
                raise
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("头像资料文件过大")
    return data


class AvatarTemplatePack:
    def __init__(self, manifest: dict[str, Any], *, root: Path, runtime: Any | None = None):
        self.manifest = manifest
        self.runtime = runtime
        revision = str(manifest.get("revision") or "")
        if not re.fullmatch(r"[a-f0-9]{40}", revision):
            raise ValueError("invalid portrait revision")
        self.root = root / "avatars" / revision
        self._lock = threading.Lock()
        self._install_lock = threading.Lock()
        self._features: list[tuple[dict[str, Any], Any, Any, tuple[int, int]]] | None = None
        self._state: dict[str, Any] = {"state": "missing", "installed": False, "message": "头像资料尚未下载"}
        self._reload()

    def _path(self, item: dict[str, Any]) -> Path:
        digest = str(item.get("git_blob_sha1") or "")
        if not re.fullmatch(r"[a-f0-9]{40}", digest):
            raise ValueError("invalid portrait hash")
        return self.root / (digest + ".png")

    def _reload(self) -> None:
        templates = self.manifest["templates"]
        valid = sum(
            1
            for item in templates
            if self._path(item).is_file() and git_blob_hash(self._path(item).read_bytes()) == item["git_blob_sha1"]
        )
        ready = bool(templates and valid == len(templates) and (self.root / "LICENSE.txt").is_file())
        with self._lock:
            self._state = {
                "state": "ready" if ready else "missing",
                "installed": ready,
                "message": "头像资料已下载并校验" if ready else "头像资料尚未准备完成，文字识别仍可使用",
                "downloaded": valid,
                "total": len(templates),
                "bytes": sum(int(item.get("bytes") or 0) for item in templates),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = {
                **self._state,
                "source_name": self.manifest["source_name"],
                "source_url": self.manifest["source_url"],
                "revision": self.manifest["revision"],
                "license_id": self.manifest["license_id"],
            }
        if self.runtime is not None:
            runtime = self.runtime.status()
            result["runtime"] = runtime
            if not runtime["available"]:
                result.update(installed=False, state=runtime["state"], message=runtime["message"])
        return result

    def load_cv2(self):
        if self.runtime is not None:
            return self.runtime.load()
        import cv2

        return cv2

    def install(self, *, downloader=_download) -> dict[str, Any]:
        with self._install_lock:
            return self._install(downloader=downloader)

    def _install(self, *, downloader) -> dict[str, Any]:
        if self.runtime is not None and not self.runtime.install()["available"]:
            return self.status()
        if self.status()["installed"]:
            return self.status()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self._lock:
                self._state.update(state="installing", message="正在下载并校验头像资料")
            license_data = downloader(self.manifest["license_url"])
            if b"GNU GENERAL PUBLIC LICENSE" not in license_data:
                raise ValueError("头像资料许可证校验失败")
            (self.root / "LICENSE.txt").write_bytes(license_data)
            base = (
                f"https://raw.githubusercontent.com/OneDragon-Anything/StarRailOneDragon/{self.manifest['revision']}/"
            )

            def install_one(item):
                target = self._path(item)
                if target.is_file() and git_blob_hash(target.read_bytes()) == item["git_blob_sha1"]:
                    return
                path = str(item["path"])
                if not re.fullmatch(r"assets/template/character_avatar/[a-z0-9_]+/raw\.png", path):
                    raise ValueError("invalid portrait source path")
                data = downloader(base + path)
                if len(data) != item["bytes"] or git_blob_hash(data) != item["git_blob_sha1"]:
                    raise ValueError(f"头像资料校验失败：{item['name']}")
                from PIL import Image

                with Image.open(BytesIO(data)) as image:
                    if image.format != "PNG" or max(image.size) > 1024:
                        raise ValueError("invalid portrait image")
                    image.verify()
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(data)
                os.replace(temporary, target)
                with self._lock:
                    self._state["downloaded"] = int(self._state.get("downloaded") or 0) + 1

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(install_one, self.manifest["templates"]))
            (self.root / "SOURCE.json").write_text(
                json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._features = None
            self._reload()
        except Exception as exc:
            with self._lock:
                self._state.update(state="error", installed=False, message=f"头像资料准备失败，文字识别仍可使用：{exc}")
        return self.status()

    def match(self, image: Any, *, page_type: str, rows: list[float] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Find HUD portraits only, rejecting weak or spatially ambiguous matches."""
        if not self.status()["installed"]:
            return [], {"available": False, "reason": "portrait_pack_missing"}
        if page_type not in {"unknown", "exploration_hud", "map_or_quest", "combat", "team_setup"}:
            return [], {"available": True, "reason": "unsupported_portrait_page"}
        try:
            cv2 = self.load_cv2()
            import numpy as np

            sift = cv2.SIFT_create(nfeatures=800)
            if self._features is None:
                loaded = []
                for item in self.manifest["templates"]:
                    # Recheck content at the point of loading, not just download.
                    data = self._path(item).read_bytes()
                    if git_blob_hash(data) != item["git_blob_sha1"]:
                        raise ValueError("portrait cache hash mismatch")
                    template = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                    keypoints, descriptors = sift.detectAndCompute(template, None)
                    if descriptors is not None and len(keypoints) >= 8:
                        loaded.append((item, keypoints, descriptors, template.shape[:2]))
                self._features = loaded
            width, height = image.size
            rois = [(int(width * 0.78), int(height * 0.12), width, int(height * 0.82))]
            if rows and len(rows) == 4 and page_type in {"exploration_hud", "map_or_quest", "unknown"}:
                half = (rows[-1] - rows[0]) / 3 * .46
                rois = [
                    (int(width * .915), max(0, int(y - half)), int(width * .985), min(height, int(y + half)))
                    for y in rows
                ]
            if page_type == "combat":
                rois.append((0, int(height * 0.67), width, height))
            matches = []
            matcher = cv2.BFMatcher(cv2.NORM_L2)
            for left, top, right, bottom in rois:
                gray = cv2.cvtColor(np.asarray(image.crop((left, top, right, bottom))), cv2.COLOR_RGB2GRAY)
                # The helper downsizes high-DPI screens. Detect at 2x so small
                # HUD portraits have enough keypoints; geometry stays in the
                # original image coordinate system and thresholds stay strict.
                scale = 1 if width >= 1920 else 2
                enlarged = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                target_kps, target_desc = sift.detectAndCompute(enlarged, None)
                if target_desc is None or len(target_kps) < 8:
                    continue
                for item, keypoints, descriptors, (th, tw) in self._features:
                    pairs = matcher.knnMatch(descriptors, target_desc, k=2)
                    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.65 * pair[1].distance]
                    if len(good) < 8:
                        continue
                    src = np.float32([keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                    dst = (np.float32([target_kps[m.trainIdx].pt for m in good]) / scale).reshape(-1, 1, 2)
                    matrix, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
                    count = int(inliers.sum()) if inliers is not None else 0
                    if matrix is None or count < 8 or count / len(good) < 0.75:
                        continue
                    points = src[inliers.ravel().astype(bool)].reshape(-1, 2)
                    if float(np.ptp(points[:, 0]) * np.ptp(points[:, 1])) < tw * th * 0.12:
                        continue
                    corners = cv2.perspectiveTransform(
                        np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2), matrix
                    ).reshape(-1, 2)
                    if not np.isfinite(corners).all() or not cv2.isContourConvex(corners.astype(np.float32)):
                        continue
                    x0, y0 = corners.min(axis=0)
                    x1, y1 = corners.max(axis=0)
                    if x0 < -3 or y0 < -3 or x1 > gray.shape[1] + 3 or y1 > gray.shape[0] + 3:
                        continue
                    if not (width * 0.015 <= x1 - x0 <= width * 0.14 and height * 0.025 <= y1 - y0 <= height * 0.22):
                        continue
                    ratio = (x1 - x0) / max(1, y1 - y0) / (tw / th)
                    if not 0.65 <= ratio <= 1.5:
                        continue
                    matches.append(
                        {
                            "entity_id": item["entity_id"],
                            "name": item["name"],
                            "identity_scope": "hud_portrait",
                            "evidence": "opencv_sift_ransac_upstream_portrait",
                            "confidence": "mixed",
                            "inliers": count,
                            "region": {
                                "left": float(left + x0) / width,
                                "top": float(top + y0) / height,
                                "right": float(left + x1) / width,
                                "bottom": float(top + y1) / height,
                            },
                            "source_revision": self.manifest["revision"],
                        }
                    )
            matches.sort(key=lambda item: -item["inliers"])
            accepted = []
            for item in matches:
                region = item["region"]
                ambiguous = any(
                    other["entity_id"] != item["entity_id"]
                    and abs((other["region"]["top"] + other["region"]["bottom"] - region["top"] - region["bottom"]) / 2)
                    < 0.04
                    and abs((other["region"]["left"] + other["region"]["right"] - region["left"] - region["right"]) / 2)
                    < 0.06
                    and other["inliers"] >= item["inliers"] - 3
                    for other in matches
                )
                if not ambiguous and not any(other["entity_id"] == item["entity_id"] for other in accepted):
                    accepted.append(item)
            return accepted[:8], {
                "available": True,
                "reason": "matched" if accepted else "no_reliable_portrait",
                "template_count": len(self._features),
                "ambiguous_or_weak_rejected": len(matches) - len(accepted),
            }
        except Exception as exc:
            return [], {
                "available": False,
                "reason": "portrait_runtime_unavailable",
                "error": f"{type(exc).__name__}: {exc}",
            }
