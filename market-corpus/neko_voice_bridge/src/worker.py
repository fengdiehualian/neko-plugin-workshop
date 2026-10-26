import asyncio
import time

from .speech_worker import SpeechWorker


def should_reply(event, config, epoch):
    if event.get("host_handled") or event.get("type") != "transcript.final" or event.get("epoch") != epoch:
        return False
    if time.time() * 1000 - event.get("at_ms", 0) > 15000:
        return False
    speaker = event.get("speaker", {})
    if not speaker.get("identity_verified"):
        operator = (config.get("allow_operator_confirmed") is True
                    and speaker.get("identity_source") == "operator_confirmed")
        rtc = (config.get("allow_rtc_display_names") is True
               and speaker.get("identity_source") == "rtc_signaling"
               and speaker.get("audio_source_verified") is True)
        if not (operator or rtc):
            return False
    text = event.get("text", "")
    if isinstance(event.get("wake_filter_enabled"), bool):
        return bool(text) and (not event["wake_filter_enabled"]
                              or any(word in text for word in config.get("wake_words", []) if word))
    return bool(text) and (config.get("response_mode") == "all"
                          or any(word in text for word in config.get("wake_words", []) if word))


class VoiceWorker:
    def __init__(self, bridge, backend, config):
        self.bridge, self.backend, self.config = bridge, backend, config
        self.task = None
        self.last_error = None
        self.processed = 0
        self.speech = None

    async def start(self):
        status = await self.bridge.request("/v1/status")
        if status.get("protocol") != "NVB-POC/1":
            raise RuntimeError("UNSUPPORTED_BRIDGE_PROTOCOL")
        self.cursor, self.epoch = status["sequence"], status["epoch"]
        if status.get("speech", {}).get("mode") == "neko":
            self.speech = SpeechWorker(self.bridge, context_sender=self.config.get("context_sender"))
            await self.speech.start()
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.speech:
            await self.speech.stop()
            self.speech = None
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        try:
            await self.bridge.request("/v1/cancel", {})
        finally:
            await self.backend.close()

    async def run(self):
        while True:
            try:
                batch = await self.bridge.request(f"/v1/events?after={self.cursor}")
                changed = batch["epoch"] != self.epoch
                if changed or batch["gap"]:
                    await self.backend.close()
                    self.epoch = batch["epoch"]
                self.cursor = batch["cursor"]
                if batch["gap"]:
                    self.last_error = "EVENT_GAP_SKIPPED"
                    continue
                for event in batch["events"]:
                    if not should_reply(event, self.config, self.epoch):
                        continue
                    try:
                        answer = await asyncio.wait_for(self.backend.respond(event), timeout=35)
                        status = await self.bridge.request("/v1/status")
                        if status["epoch"] != event["epoch"] or not status["listening"] or status["muted"]:
                            await self.backend.close()
                            continue
                        await self.bridge.request("/v1/reply", {
                            "utterance_id": event["utterance_id"], "epoch": event["epoch"], "text": answer,
                        })
                        self.processed += 1
                        self.last_error = None
                    except Exception:
                        self.last_error = "REPLY_FAILED_CHECK_LOCAL_CONFIG"
                        await self.backend.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.last_error = "BRIDGE_POLL_FAILED"
                await self.backend.close()
            await asyncio.sleep(0.5)
