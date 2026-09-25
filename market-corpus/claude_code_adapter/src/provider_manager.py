"""Claude Code Adapter — 多供应商（Provider）管理。

参考 cc-switch 的供应商切换思路：每个 provider 携带一组环境变量
（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY /
ANTHROPIC_MODEL 等），切换后通过 CLI 子进程的 env 注入生效，
不修改用户全局的 ~/.claude/settings.json，更安全且可随时回退。

约束（对齐 cc-switch 的已知问题修复）：
- ANTHROPIC_AUTH_TOKEN 与 ANTHROPIC_API_KEY 只注入其一，
  两者同时存在会触发 Claude Code 的
  "Both ANTHROPIC_AUTH_TOKEN and ANTHROPIC_API_KEY set" 警告（cc-switch #4919）。

Provider 注册表持久化在插件目录下的 providers.json（已 gitignore，
避免真实 token 误提交；providers.example.json 作为模板随仓库分发）。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

# 允许 provider 注入的环境变量白名单（防止任意环境变量注入）
ALLOWED_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "OPENROUTER_API_KEY",
    }
)

# 认证键互斥组：同一 provider 只保留其中一个
_AUTH_KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")

PROVIDERS_FILE = "providers.json"
EXAMPLE_FILE = "providers.example.json"


@dataclass
class Provider:
    """单个 Claude Code 供应商配置。"""

    name: str
    """供应商标识（唯一键，小写保存）。"""

    display_name: str = ""
    """展示名称。"""

    env: dict[str, str] = field(default_factory=dict)
    """环境变量（仅限白名单键）。"""

    def to_dict(self, *, mask_secrets: bool = False) -> dict[str, Any]:
        env: dict[str, str] = dict(self.env)
        if mask_secrets:
            for key in _AUTH_KEYS + ("OPENROUTER_API_KEY",):
                if env.get(key):
                    value = env[key]
                    env[key] = value[:4] + "****" if len(value) > 4 else "****"
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "env": env,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provider":
        raw_env = data.get("env") if isinstance(data.get("env"), dict) else {}
        env = {
            str(k): str(v)
            for k, v in raw_env.items()
            if str(k) in ALLOWED_ENV_KEYS and str(v)
        }
        return cls(
            name=str(data.get("name", "")).strip().lower(),
            display_name=str(data.get("display_name", "")),
            env=env,
        )


class ProviderManager:
    """供应商注册表 + 激活状态管理。

    线程安全：所有读写均在锁内完成（插件运行在 asyncio 单线程，
    加锁主要防御未来的多线程调用）。
    """

    def __init__(self, base_dir: str, logger: Any = None) -> None:
        self._base_dir = base_dir
        self.logger = logger
        self._lock = threading.Lock()
        self._providers: dict[str, Provider] = {}
        self._active: str = ""

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    @property
    def providers_file(self) -> str:
        return os.path.join(self._base_dir, PROVIDERS_FILE)

    def load(self) -> None:
        """加载 providers.json；不存在时尝试从 example 模板初始化。"""
        with self._lock:
            self._providers = {}
            self._active = ""

            path = self.providers_file
            if not os.path.isfile(path):
                example = os.path.join(self._base_dir, EXAMPLE_FILE)
                if os.path.isfile(example):
                    path = example
                else:
                    return

            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                if self.logger is not None:
                    try:
                        self.logger.warning("Failed to load providers.json: {}", e)
                    except Exception:
                        pass
                return

            providers = data.get("providers") if isinstance(data, dict) else data
            if isinstance(providers, list):
                for item in providers:
                    if isinstance(item, dict):
                        provider = Provider.from_dict(item)
                        if provider.name:
                            self._providers[provider.name] = provider
            if isinstance(data, dict):
                active = str(data.get("active_provider", ""))
                if active in self._providers:
                    self._active = active

    def _save(self) -> None:
        """写回 providers.json（锁内调用）。"""
        payload = {
            "active_provider": self._active,
            "providers": [p.to_dict() for p in self._providers.values()],
        }
        try:
            tmp_path = self.providers_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.providers_file)
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.warning("Failed to save providers.json: {}", e)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_providers(self) -> list[dict[str, Any]]:
        """列出所有供应商（token 打码）。"""
        with self._lock:
            return [
                {**p.to_dict(mask_secrets=True), "active": p.name == self._active}
                for p in self._providers.values()
            ]

    def get_active(self) -> Optional[Provider]:
        """返回当前激活的 provider；未激活时返回 None。"""
        with self._lock:
            if not self._active:
                return None
            return self._providers.get(self._active)

    def get_active_name(self) -> str:
        with self._lock:
            return self._active

    # ------------------------------------------------------------------
    # 变更
    # ------------------------------------------------------------------

    def add_or_update(self, provider: Provider) -> Provider:
        """注册或更新供应商。"""
        if not provider.name:
            raise ValueError("provider name 不能为空")
        with self._lock:
            self._providers[provider.name] = provider
            self._save()
        return provider

    def remove(self, name: str) -> bool:
        """删除供应商；若删除的是激活项则清空激活状态。"""
        key = name.strip().lower()
        with self._lock:
            if key not in self._providers:
                return False
            del self._providers[key]
            if self._active == key:
                self._active = ""
            self._save()
            return True

    def set_active(self, name: str) -> Provider:
        """切换激活供应商。name 为空字符串表示清除激活（回退官方默认）。"""
        key = name.strip().lower()
        with self._lock:
            if not key:
                self._active = ""
                self._save()
                return Provider(name="")
            provider = self._providers.get(key)
            if provider is None:
                raise KeyError(f"provider not found: {name}")
            self._active = key
            self._save()
            return provider

    def env_overrides(self) -> dict[str, str]:
        """返回激活 provider 的环境变量覆盖（供 CLI 子进程注入）。

        认证键互斥：同时配置 AUTH_TOKEN 和 API_KEY 时只保留 AUTH_TOKEN，
        避免 Claude Code 的双认证键警告（cc-switch #4919）。
        """
        provider = self.get_active()
        if provider is None:
            return {}
        env = {k: v for k, v in provider.env.items() if k in ALLOWED_ENV_KEYS}
        if all(env.get(k) for k in _AUTH_KEYS):
            env.pop("ANTHROPIC_API_KEY")
        return env
