from pathlib import Path


def test_tick_blocks_scene_polling_until_manager_identity_is_compatible():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    start = source.index("    def _tick_locked(self) -> None:")
    end = source.index("    def _ensure_running_game_context", start)
    tick = source[start:end]

    guard = "if not self.data_layer_manager.can_consume_scene():"
    poll = "new_state = self.client.poll()"
    assert tick.index(guard) < tick.index(poll)
    assert "self._reset_runtime_after_data_layer_loss()" in tick[tick.index(guard):tick.index(poll)]
    disconnect = "if not new_state.connected:"
    invalidate = "self.data_layer_manager.invalidate_external_after_disconnect()"
    assert tick.index(poll) < tick.index(disconnect) < tick.index(invalidate)
    disconnect_block = tick[tick.index(disconnect):tick.index("with self._state_lock:", tick.index(disconnect))]
    assert "self._reset_runtime_after_data_layer_loss()" in disconnect_block
    assert "return" in disconnect_block
    validate = "self.data_layer_manager.validate_scene_instance("
    evaluate = "self.runtime.evaluate(prev, new_state)"
    assert tick.index(disconnect) < tick.index(validate) < tick.index(evaluate)
    assert "and not new_state.events_tail" in tick


def test_config_reload_failure_preserves_the_live_config():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    read_start = source.index("    async def _read_config")
    read_end = source.index("    async def _reload_config", read_start)
    read_source = source[read_start:read_end]
    reload_start = read_end
    reload_end = source.index("    def _apply_config", reload_start)
    reload_source = source[reload_start:reload_end]
    change_start = source.index("    async def on_config_change")
    change_end = source.index("    def _loop", change_start)
    change_source = source[change_start:change_end]

    assert "if not use_defaults_on_error:" in read_source
    assert "return None" in read_source
    assert "if cfg is None:" in reload_source
    assert reload_source.index("return False") < reload_source.index("self._apply_config")
    assert "cfg = await self._read_config()" in change_source
    assert "if cfg is None:" in change_source
    assert "current config preserved" in change_source


def test_data_layer_loss_helper_restores_context_once_and_resets_runtime():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    start = source.index("    def _reset_runtime_after_data_layer_loss")
    end = source.index("    def _retry_context_restore", start)
    helper = source[start:end]

    assert "self.runtime.reset()" in helper
    assert "if self._context_active:" in helper
    assert "self._context_restore.request()" in helper
    assert "self._retry_context_restore()" in helper


def test_context_restore_checks_dispatch_result_and_retries_before_disabled_return():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    retry_start = source.index("    def _retry_context_restore")
    retry_end = source.index("    def _drain_context_restore", retry_start)
    retry_source = source[retry_start:retry_end]
    tick_start = source.index("    def _tick_locked(self) -> None:")
    tick_end = source.index("    def _reset_runtime_after_data_layer_loss", tick_start)
    tick_source = source[tick_start:tick_end]

    assert "restored = bool(self.dispatcher.push_context(CBQ_RESTORE_INSTRUCTIONS))" in retry_source
    assert "finish_attempt(success=restored" in retry_source
    assert retry_source.index("if restored:") < retry_source.index("self._context_active = False")
    assert tick_source.index("self._retry_context_restore()") < tick_source.index("if not self.cfg.enabled:")


def test_tick_config_and_dry_run_changes_share_the_lifecycle_lock():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")

    tick_start = source.index("    def _tick(self) -> None:")
    tick_end = source.index("    def _tick_locked", tick_start)
    tick_wrapper = source[tick_start:tick_end]
    apply_start = source.index("    def _apply_config")
    apply_end = source.index("    @lifecycle(id=\"startup\")", apply_start)
    action_start = source.index("    async def set_dry_run")
    action_end = source.index("    @ui.action(id=\"pause\"", action_start)

    assert "with self._lifecycle_lock:" in tick_wrapper
    assert "with self._lifecycle_lock:" in source[apply_start:apply_end]
    assert "with self._lifecycle_lock:" in source[action_start:action_end]


def test_shutdown_drains_context_restore_before_returning():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    shutdown_start = source.index("    def shutdown")
    shutdown_end = source.index("    @lifecycle(id=\"config_change\")", shutdown_start)
    shutdown = source[shutdown_start:shutdown_end]
    finish_start = source.index("    def _finish_shutdown")
    finish_end = source.index("    def _tick(self)", finish_start)
    finish = source[finish_start:finish_end]
    drain_start = source.index("    def _drain_context_restore")
    drain_end = source.index("    def _ensure_running_game_context", drain_start)
    drain = source[drain_start:drain_end]

    assert "worker.join(timeout=self._shutdown_join_timeout_seconds)" in shutdown
    assert '"status": "shutdown_pending"' in shutdown
    assert "self._drain_context_restore()" in finish
    assert '"context_restore_pending": self._context_restore.pending' in finish
    assert "max_attempts: int = 3" in drain
    assert "self._retry_context_restore(force=True)" in drain


def test_startup_rejects_overlapping_same_or_replacement_instance():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    startup_start = source.index("    async def startup")
    startup_end = source.index("    @lifecycle(id=\"shutdown\")", startup_start)
    startup = source[startup_start:startup_end]
    finish_start = source.index("    def _finish_shutdown")
    finish_end = source.index("    def _tick(self)", finish_start)
    finish = source[finish_start:finish_end]

    assert "self._thread.is_alive() or not self._shutdown_complete.is_set()" in startup
    assert "previous plugin runtime is still shutting down" in startup
    assert "try_claim_instance_gate(" in startup
    assert "another plugin instance is still shutting down" in startup
    assert startup.index("try_claim_instance_gate(") < startup.index("self._stop.clear()")
    assert "self._release_instance_gate()" in finish


def test_config_disabling_output_schedules_context_restoration():
    root = Path(__file__).resolve().parents[1]
    source = (root / "__init__.py").read_text(encoding="utf-8-sig")
    start = source.index("    def _apply_config")
    end = source.index("    @lifecycle(id=\"startup\")", start)
    apply_source = source[start:end]

    assert "previous.enabled and not cfg.enabled" in apply_source
    assert "not previous.dry_run and cfg.dry_run" in apply_source
    assert "self._reset_runtime_after_data_layer_loss()" in apply_source

    action_start = source.index("    async def set_dry_run")
    action_end = source.index("    @ui.action(id=\"pause\"", action_start)
    action_source = source[action_start:action_end]
    assert "not previous_dry_run and self.cfg.dry_run" in action_source
    assert "self._reset_runtime_after_data_layer_loss()" in action_source
