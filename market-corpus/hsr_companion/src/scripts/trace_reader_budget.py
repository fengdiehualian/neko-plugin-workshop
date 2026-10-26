"""True frame/path, native coverage and cooperative time; no policy changes.

Only hashes and capture metadata are retained, never source images. Each
wrapper calls the original once and cannot authorize a candidate or change a
budget. The existing isolated harness restores the installed observer.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import threading
import time
from functools import wraps
from unittest.mock import patch

from . import trace_character_handoff as handoff


class ReaderBudgetProbe(handoff.ReviewProbe):
    def __enter__(self):
        super().__enter__()
        from plugin.plugins.hsr_companion.adapters import local_vision
        self.frame_metadata, self.cooperative_seconds = {}, {}
        self.reader_serial = 0
        adapter = self.owner._vision
        original_fetch = self.owner._assistant_client.fetch_frame

        def fetch(*args, **kw):
            frame = original_fetch(*args, **kw)
            if frame is not None:
                digest = hashlib.sha256(frame.image_bytes).hexdigest()
                self.frame_metadata[id(frame.image_bytes)] = {'frame_seq': frame.frame_seq, 'captured_at': frame.timestamp,
                    'game_pid': frame.game.get('pid'), 'payload_sha256': digest}
                if len(self.frame_metadata) > 96:
                    self.frame_metadata.pop(next(iter(self.frame_metadata)))
            return frame

        self.stack.enter_context(patch.object(self.owner._assistant_client, 'fetch_frame', fetch))
        for name in ('analyze', '_analyze_scene'):
            original = getattr(adapter, name)

            @wraps(original)
            def path(payload, *args, _original=original, _name=name, **kw):
                self.reader_serial += 1
                digest = hashlib.sha256(payload).hexdigest()
                frame_info = self.frame_metadata.get(id(payload), {})
                if frame_info.get('payload_sha256') != digest:
                    frame_info = {}
                context = {'read_id': self.reader_serial, 'path': _name, 'payload_sha256': digest,
                           **frame_info, 'frame_matched': bool(frame_info), 'binding': 'payload_object_identity'}
                token = self.read_context.set(context)
                started = time.monotonic()
                self.emit('reader_path_started', reader=context)
                try:
                    result = _original(payload, *args, **kw)
                    self.emit('reader_path_finished', reader=context, seconds=round(time.monotonic() - started, 6),
                        reading=result.get('recognition', {}).get('text_reader'),
                        facts=result.get('screen_facts', {}).get('facts'),
                        page=result.get('page', {}).get('page_type'), ocr_error=result.get('ocr', {}).get('error'))
                    return result
                except BaseException as exc:
                    self.emit('reader_path_exit', reader=context, exception=type(exc).__name__)
                    raise
                finally:
                    self.read_context.reset(token)

            self.stack.enter_context(patch.object(adapter, name, path))

        queue = adapter._review_queue
        original_pump = queue.pump

        def pump():
            pending = queue._pending
            active = bool(pending is not None and not queue._running and queue._producer == threading.get_ident())
            context = self.read_context.get()
            started = time.monotonic()
            try:
                return original_pump()
            finally:
                if active:
                    elapsed = time.monotonic() - started
                    key = (context or {}).get('read_id')
                    self.cooperative_seconds[key] = self.cooperative_seconds.get(key, 0) + elapsed
                    self.emit('checkpoint_work', reader=context, callback=pending[1].__qualname__,
                        seconds=round(elapsed, 6), cancelled=pending[0].cancelled())

        self.stack.enter_context(patch.object(queue, 'pump', pump))
        original_read = local_vision.read_native_text

        def read(backend, image, **kw):
            context = self.read_context.get()
            key = (context or {}).get('read_id')
            started = time.monotonic()
            prior_coop = self.cooperative_seconds.get(key, 0)
            cancelled = kw.get('cancelled')

            def checkpoint():
                before = time.monotonic()
                result = cancelled() if cancelled is not None else False
                caller = inspect.currentframe().f_back
                try:
                    loop_frame = caller.f_back
                    if loop_frame.f_code.co_name == 'read_native_text':
                        local = loop_frame.f_locals
                        self.emit('reader_checkpoint', reader=context, line=loop_frame.f_lineno,
                            elapsed=round(time.monotonic() - started, 6),
                            checkpoint_seconds=round(time.monotonic() - before, 6),
                            cooperative_seconds=round(self.cooperative_seconds.get(key, 0) - prior_coop, 6),
                            completed=local.get('completed'), total=len(local.get('tiles') or []),
                            tile=[local.get(k) for k in ('left', 'top', 'right', 'bottom')], stopped=bool(result))
                finally:
                    del caller
                return result

            try:
                text, boxes, details = original_read(backend, image, **{**kw, 'cancelled': checkpoint})
                self.emit('native_reader_finished', reader=context, size=list(image.size), details=details,
                    cooperative_seconds=round(self.cooperative_seconds.get(key, 0) - prior_coop, 6),
                    attribute_input_boxes=[b for b in boxes if float(b.get('left') or 0) >= image.width * .55
                                          and float(b.get('top') or 0) <= image.height * .8])
                return text, boxes, details
            except BaseException as exc:
                self.emit('native_reader_exit', reader=context, exception=type(exc).__name__,
                          seconds=round(time.monotonic() - started, 6))
                raise

        self.stack.enter_context(patch.object(local_vision, 'read_native_text', read))
        return self


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--signals', required=True)
    parser.add_argument('--port', type=int, default=8455)
    parser.add_argument('--on-control-change', action='store_true')
    parser.add_argument('--preload-backend', action='store_true')
    args = parser.parse_args()
    args.on_page_discovery = not args.on_control_change
    with patch.object(handoff, 'ReviewProbe', ReaderBudgetProbe):
        asyncio.run(handoff.run(args))
