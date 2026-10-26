import asyncio
import time

from .diagnostics import log_failure, speech_failure
from .neko_speech import NekoSpeech


class SpeechWorker:
    def __init__(self, bridge, speech=None, context_sender=None):
        self.bridge = bridge
        self.speech = speech or NekoSpeech(context_sender=context_sender, cable_progress=self.cable_progress)
        self.task = None
        self.active = None
        self.last_error = None
        self.last_diagnostic = None
        self.error_history = []

    async def cable_progress(self, job_id, text):
        return await self.bridge.request("/v1/speech/progress", {"id": job_id, "text": text})

    async def start(self):
        await self.bridge.request("/v1/speech/next", {"active_id": "handshake"})
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        try:
            await self.bridge.request("/v1/speech/disconnect", {})
        except Exception:
            self.last_error = "NEKO_SPEECH_BRIDGE_UNAVAILABLE"

    async def perform(self, job):
        try:
            result = await self.speech.handle(job)
            self.last_error = None
            self.last_diagnostic = None
        except Exception as error:
            self.last_diagnostic = speech_failure(error, job.get("kind"), getattr(self.speech, "stage", "unknown"))
            self.last_error = self.last_diagnostic["code"]
            self.error_history.append({"at_ms": int(time.time() * 1000), **self.last_diagnostic})
            self.error_history = self.error_history[-10:]
            log_failure(self.last_diagnostic)
            result = {"error": self.last_error}
        await self.bridge.request("/v1/speech/result", {"id": job["id"], **result})

    async def run(self):
        job = None
        try:
            while True:
                try:
                    if self.active and self.active.done():
                        # Consume transport errors without logging raw provider details.
                        if not self.active.cancelled() and self.active.exception():
                            self.last_error = "NEKO_SPEECH_DELIVERY_FAILED"
                        self.active, job = None, None
                    response = await self.bridge.request("/v1/speech/next", {"active_id": job["id"] if job else None})
                    if job and not response.get("active"):
                        self.active.cancel()
                        await asyncio.gather(self.active, return_exceptions=True)
                        self.active, job = None, None
                    elif not job and response.get("job"):
                        job = response["job"]
                        self.active = asyncio.create_task(self.perform(job))
                except Exception:
                    self.last_error = "NEKO_SPEECH_BRIDGE_UNAVAILABLE"
                    if self.active:
                        self.active.cancel()
                        await asyncio.gather(self.active, return_exceptions=True)
                        self.active, job = None, None
                await asyncio.sleep(0.3)
        finally:
            if self.active:
                self.active.cancel()
                await asyncio.gather(self.active, return_exceptions=True)
                self.active = None
