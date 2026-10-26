import asyncio
import sys
import threading
import uuid

import pytest
from plugin.plugins.neko_ads.desktop import LINES, DesktopController
from plugin.plugins.neko_ads.session import AdSession
from plugin.plugins.neko_ads.voice import VoiceBank


async def identity(character):
    return character.encode()


async def complete(bank):
    bank.ensure()
    if bank.task:
        await asyncio.wait_for(bank.task, 2)


@pytest.mark.asyncio
async def test_nine_lines_prepare_serially_and_never_emit_playback():
    running = maximum = 0
    calls = []

    async def synth(character, text, valid, *, emit):
        nonlocal running, maximum
        assert emit is None and valid()
        running += 1
        maximum = max(maximum, running)
        await asyncio.sleep(0)
        calls.append((character, text))
        running -= 1
        return text.encode(), "audio/wav"

    bank = VoiceBank(LINES, synth, identity)
    await bank.refresh("猫娘甲")
    await complete(bank)
    assert bank.snapshot()["state"] == "ready" and bank.snapshot()["ready"] == 9
    assert maximum == 1 and len(calls) == 9
    await bank.refresh("猫娘甲")
    await complete(bank)
    assert len(calls) == 9


def test_preparation_survives_the_sdk_temporary_startup_loop():
    async def synth(character, text, valid, *, emit):
        assert emit is None
        return b"audio", "audio/wav"

    session = AdSession()
    session.ready()
    desktop = DesktopController(session, speech_factory=synth, identity_factory=identity)

    async def startup():
        desktop.run()

    asyncio.run(startup())
    assert desktop.watchdog.done()

    async def commands():
        try:
            await desktop.poll(str(uuid.uuid4()), "甲")
            for _ in range(100):
                if desktop.bank.snapshot()["ready"] == 9:
                    break
                await asyncio.sleep(0.01)
            assert desktop.bank.snapshot()["ready"] == 9
            assert not desktop.snapshot()["active"] and desktop.hotkey is None
        finally:
            await desktop.close()

    asyncio.run(commands())


def test_concurrent_identity_checks_on_different_host_loops_never_wait_on_each_other():
    entered, release = threading.Event(), threading.Event()

    async def resolve(character):
        if character == "旧":
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
        return character.encode()

    bank = VoiceBank(LINES, None, resolve)
    thread = threading.Thread(target=lambda: asyncio.run(bank.refresh("旧")), daemon=True)
    thread.start()
    try:
        assert entered.wait(1)
        asyncio.run(asyncio.wait_for(bank.refresh("新"), 1))
        assert bank.character == "新"
    finally:
        release.set()
        thread.join(1)
    assert not thread.is_alive() and bank.character == "新"


@pytest.mark.asyncio
async def test_prepared_media_resources_are_current_only_and_playback_returns_a_small_url(tmp_path):
    class Key:
        def register(self, shortcut, callback):
            return shortcut

        def close(self):
            pass

    async def synth(character, text, valid, *, emit):
        assert emit is None
        return character.encode() * 100, "audio/wav"

    session = AdSession()
    session.ready()
    desktop = DesktopController(
        session, hotkey_factory=Key, speech_factory=synth, identity_factory=identity, voice_cache_dir=tmp_path
    )
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        await desktop.poll(owner, "甲")
        await desktop.bank.refresh("甲")
        await complete(desktop.bank)
        assert len(list(tmp_path.iterdir())) == 9
        await desktop.start(rid, owner, True, "Ctrl+Shift+1")
        result = await desktop.command("speech", {"round_id": rid, "event": "start", "sequence": 1})
        filename = result["audio_url"].rsplit("/", 1)[1]
        assert (tmp_path / filename).read_bytes() == "甲".encode() * 100 and not desktop.jobs
        old = desktop.bank.token
        await desktop.stop(rid)
        assert (tmp_path / filename).exists(), "ending a round retains ready clips for the next explicit start"
        # Switch the renderer too: its live watcher must not keep reporting 甲
        # while this test prepares resources for 乙.
        await desktop.poll(owner, "乙")
        await desktop.bank.refresh("乙")
        await complete(desktop.bank)
        assert not (tmp_path / filename).exists() and len(list(tmp_path.iterdir())) == 9
        assert await desktop.command("voice_clip", {"event": "start", "token": old}) == {}
    finally:
        await desktop.close()


@pytest.mark.asyncio
async def test_route_and_character_changes_cannot_reuse_the_old_voice():
    route = [b"voice-a"]

    async def resolve(character):
        return character.encode() + route[0]

    async def synth(character, text, valid, *, emit):
        return character.encode() + route[0], "audio/wav"

    bank = VoiceBank(LINES, synth, resolve)
    await bank.refresh("甲")
    await complete(bank)
    old = bank.token
    route[0] = b"voice-b"
    await bank.refresh("甲")
    assert bank.token != old and bank.clip("start") is None
    await complete(bank)
    assert bank.clip("start")[0].endswith(b"voice-b")
    await bank.refresh("乙")
    assert bank.clip("start") is None
    await complete(bank)
    assert bank.clip("start")[0].startswith("乙".encode())


@pytest.mark.asyncio
async def test_failed_line_is_visible_and_retry_only_prepares_missing_clips():
    failed = [True]
    calls = []

    async def synth(character, text, valid, *, emit):
        calls.append(text)
        if text == LINES["fake"][0] and failed[0]:
            raise RuntimeError("private-provider-key-must-never-appear")
        return b"audio", "audio/wav"

    bank = VoiceBank(LINES, synth, identity)
    await bank.refresh("甲")
    await complete(bank)
    assert bank.snapshot()["ready"] == 8 and bank.snapshot()["failed"] == ["fake"]
    assert "private-provider" not in str(bank.snapshot())
    failed[0] = False
    bank.retry()
    await complete(bank)
    assert bank.snapshot()["ready"] == 9 and len(calls) == 10


@pytest.mark.asyncio
async def test_exit_cancels_preparation_and_polling_cannot_restart_it():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def synth(character, text, valid, *, emit):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    bank = VoiceBank(LINES, synth, identity)
    await bank.refresh("甲")
    bank.ensure()
    await entered.wait()
    bank.pause()
    await asyncio.sleep(0)
    assert cancelled.is_set() and bank.task is None
    await bank.refresh("甲")
    bank.ensure()
    assert bank.task is None and bank.snapshot()["state"] == "paused"


@pytest.mark.asyncio
async def test_exit_during_first_identity_lookup_stays_paused():
    entered, release = asyncio.Event(), asyncio.Event()

    async def resolve(character):
        entered.set()
        await release.wait()
        return b"voice"

    async def synth(*args, **kwargs):
        pytest.fail("an exited preparation must not restart")

    bank = VoiceBank(LINES, synth, resolve)
    task = asyncio.create_task(bank.refresh("甲"))
    await entered.wait()
    bank.pause()
    release.set()
    await task
    bank.ensure()
    assert bank.task is None and bank.snapshot()["state"] == "paused"


@pytest.mark.asyncio
async def test_busy_and_voice_off_cancel_preparation_without_losing_ready_clips():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def synth(character, text, valid, *, emit):
        if text == LINES["start"][0]:
            return b"ready", "audio/wav"
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    bank = VoiceBank(LINES, synth, identity)
    await bank.refresh("甲")
    bank.ensure()
    await entered.wait()
    bank.set_busy(True)
    await asyncio.sleep(0)
    assert cancelled.is_set() and bank.clip("start")[0] == b"ready"
    bank.set_busy(False)
    bank.enable(False)
    bank.ensure()
    assert bank.snapshot()["state"] == "disabled" and bank.clip("start") is None


@pytest.mark.asyncio
async def test_prepared_audio_is_reused_in_game_and_stop_cancels_all_work():
    class Key:
        def register(self, shortcut, callback):
            self.callback = callback
            return shortcut

        def close(self):
            pass

    calls = []
    identity_calls = []

    async def resolve(character):
        identity_calls.append(character)
        return await identity(character)

    async def synth(character, text, valid, *, emit):
        calls.append(text)
        return b"audio", "audio/wav"

    session = AdSession()
    session.ready()
    desktop = DesktopController(session, hotkey_factory=Key, speech_factory=synth, identity_factory=resolve)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    await desktop.poll(owner, "甲")
    await desktop.bank.refresh("甲")
    await complete(desktop.bank)
    checks = len(identity_calls)
    assert not desktop.snapshot()["active"] and desktop.hotkey is None and not desktop.jobs
    clip = await desktop.command("voice_clip", {"event": "start", "token": desktop.bank.token})
    assert clip["audio"] and len(calls) == 9
    await desktop.start(rid, owner, True, "Ctrl+Shift+1")
    first = await desktop.command("speech", {"round_id": rid, "event": "start", "sequence": 1})
    assert desktop.speech_result(first["job_id"])["audio_part"]
    second = await desktop.command("speech", {"round_id": rid, "event": "fake", "sequence": 2})
    assert desktop.speech_result(second["job_id"])["audio_part"], (
        "no second backend cooldown may reject the serialized player"
    )
    assert len(calls) == 9
    assert len(identity_calls) == checks, "playback must not repeat provider/configuration lookups"
    await desktop.stop(rid)
    assert desktop.bank.snapshot()["state"] == "paused" and not desktop.jobs
    assert await desktop.command("speech", {"round_id": rid, "event": "start", "sequence": 3}) == {}
    await desktop.close()


@pytest.mark.asyncio
async def test_prepared_wav_starts_in_bounded_pcm_packets_and_exit_discards_the_rest():
    import base64
    import io
    import wave

    data = bytes(range(256)) * 1000
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(data)

    async def synth(*args, **kwargs):
        return output.getvalue(), "audio/wav"

    class Key:
        def register(self, shortcut, callback):
            return shortcut

        def close(self):
            pass

    session = AdSession()
    session.ready()
    desktop = DesktopController(session, hotkey_factory=Key, speech_factory=synth, identity_factory=identity)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        await desktop.poll(owner, "甲")
        await desktop.bank.refresh("甲")
        await complete(desktop.bank)
        token = desktop.bank.token
        collected = bytearray()
        offset = 0
        while True:
            result = await desktop.command("voice_clip", {"event": "start", "token": token, "offset": offset})
            part = base64.b64decode(result["audio"])
            assert len(part) <= 48000
            collected.extend(part)
            offset = result["next_offset"]
            if result["ready"]:
                break
        assert bytes(collected) == output.getvalue()
        await desktop.start(rid, owner, True, "Ctrl+Shift+1")
        job = desktop.speech(rid, "start", 1)["job_id"]
        collected.clear()
        while True:
            result = desktop.speech_result(job)
            part = base64.b64decode(result["pcm"])
            assert len(part) <= 48000 and result["sample_rate"] == 48000
            collected.extend(part)
            if result["ready"]:
                break
        assert bytes(collected) == data
        job = desktop.speech(rid, "fake", 2)["job_id"]
        assert not desktop.speech_result(job)["ready"]
        await desktop.stop(rid)
        assert desktop.speech_result(job) == {"ready": True}
    finally:
        await desktop.close()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows global key")
async def test_native_exit_key_cancels_silent_preparation_too():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def synth(character, text, valid, *, emit):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    session = AdSession()
    session.ready()
    desktop = DesktopController(session, speech_factory=synth, identity_factory=identity)
    owner, rid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        await desktop.poll(owner, "甲")
        await desktop.bank.refresh("甲")
        desktop.bank.ensure()
        await entered.wait()
        try:
            await desktop.start(rid, owner, True, "Ctrl+Alt+Shift+F11")
        except ValueError:
            pytest.skip("Test combination is already in use")
        key = desktop.hotkey
        key_thread = key.thread
        assert key._user32.PostThreadMessageW(key.thread_id, 0x0312, 1, 0)
        await asyncio.wait_for(cancelled.wait(), 1)
        for _ in range(100):
            if not key_thread.is_alive():
                break
            await asyncio.sleep(0.01)
        assert not desktop.snapshot()["active"] and desktop.hotkey is None and not key_thread.is_alive()
        assert desktop.bank.snapshot()["state"] == "paused"
    finally:
        await desktop.close()
