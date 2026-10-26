"""Own an event loop independent of the host's short-lived lifecycle loops."""
import asyncio
import threading

from .connection import Connection


class ConnectionService:
    def __init__(self, path, legacy_path=None, *, connection_factory=Connection, context_sender=None):
        self.path, self.legacy_path = path, legacy_path
        self.connection_factory = connection_factory
        self.context_sender = context_sender
        self.loop = asyncio.new_event_loop()
        self.connection = None
        self.thread = threading.Thread(target=self._run, name="neko-voice-connection", daemon=True)
        self.thread.start()
        self.closed = False

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    async def _dispatch(self, method, args):
        if self.connection is None:
            self.connection = self.connection_factory(self.path)
            self.connection.context_sender = self.context_sender
            await self.connection.start(self.legacy_path)
        if method == "snapshot":
            return self.connection.snapshot()
        return await getattr(self.connection, method)(*args)

    async def call(self, method, *args):
        if self.closed:
            raise RuntimeError("CONNECTION_SERVICE_CLOSED")
        future = asyncio.run_coroutine_threadsafe(self._dispatch(method, args), self.loop)
        return await asyncio.wrap_future(future)

    async def close(self):
        if self.closed:
            return
        try:
            await self.call("close")
        finally:
            self.closed = True
            self.loop.call_soon_threadsafe(self.loop.stop)
            await asyncio.to_thread(self.thread.join, 10)
