"""User initiated optional onboarding, independent of the native work queue."""

from agent_mc_autonomy.configuration import Settings
from agent_mc_autonomy.mailbox import Mailbox
from agent_mc_autonomy.ownership import session_is_owned
from agent_mc_autonomy.recovery import require_no_pending_acknowledgement
from plugin.sdk.plugin import Err, Ok, plugin_entry, ui

from .prepared_runtime import resolve_host_runtime
from .qiandengji_account import AccountError, account_action, prepare_login, read_identity


def require_idle(plugin, settings):
    directory = settings.data_root(plugin.data_path()) / "sessions" / settings.identity / "host"
    if session_is_owned(directory) or plugin._worker and plugin._worker.snapshot().get("worker_alive"):
        raise AccountError("account_runtime_active")
    if directory.exists():
        Mailbox(directory).check_uncertain()
        require_no_pending_acknowledgement(directory)


class QiandengjiPanel:
    @ui.action(label="千灯纪网站账户接入")
    @plugin_entry(id="qiandengji_account", name="千灯纪网站账户接入", kind="service", timeout=85,
                  description="显式邀请注册、查状态、激活或导入；只收本机私有文件路径，不收密码或邀请码正文，不启动游戏。",
                  input_schema={"type": "object", "properties": {
                      "action": {"enum": ["register", "status", "activate", "import"]},
                      "identity_file": {"type": "string", "maxLength": 4096},
                      "username": {"type": "string", "maxLength": 16},
                      "invite_url_file": {"type": "string", "maxLength": 4096}}, "required": ["action", "identity_file"]})
    async def qiandengji_account(self, action: str, identity_file: str, username: str = "", invite_url_file: str = "", **_):
        try:
            if action not in {"register", "status", "activate", "import"}:
                raise AccountError("invalid_account_action")
            raw = await self.config.dump()
            effective, _ = resolve_host_runtime(raw)
            settings = Settings.parse(effective)
            if action != "status":
                require_idle(self, settings)
            if action == "register" and username == settings.username:
                history = settings.data_root(self.data_path()) / "sessions" / settings.identity / "host"
                if settings.login_config_file or (history / "status.json").exists():
                    raise AccountError("existing_game_role_use_original_connection")
            result = await account_action("status" if action == "import" else action, identity_file,
                                          username=username, invite_url_file=invite_url_file, node=settings.node)
            if action == "import":
                if not result["ready"]:
                    raise AccountError("account_not_ready_query_status")
                path, identity = read_identity(identity_file)
                connection = {"host": "micro.kangqiang.site", "port": 25702, "username": identity["username"],
                              "auth": "offline", "version": "1.21.1", "connection_type": "custom"}
                desired = Settings.parse({**raw, "server": {**raw.get("server", {}), **connection}})
                require_idle(self, settings)
                require_idle(self, desired)
                if settings.runtime_mode == "external":
                    raise AccountError("import_requires_embedded_mode")
                login = prepare_login(path, identity, self.data_path())
                saved = await self.save_settings({"server": {**connection, "login_config_file": str(login)},
                                                  "qiandengji": {"identity_file": str(path)}})
                if isinstance(saved, Err):
                    return saved
                result["imported"] = True
            return Ok(result)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            code = str(error) if isinstance(error, AccountError) else "account_operation_not_ready"
            return Err({"code": code, "message": "接入未完成（" + code + "）。保留原身份文件；pending/unknown 先查状态。已有游戏账号沿用原接入，切换前先停止并等动作对账。"})
