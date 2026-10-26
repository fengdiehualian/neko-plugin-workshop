"""One resident exploration -> attribute request, with causal read-only probes.

Runs the real source tool on real frames but never messages the host cat. Reuses
the unchanged-helper isolation harness, including installed-observer restoration.
No recognition value, timeout, admission decision or worker ordering is replaced.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import verify_page_activity_candidate as harness
from .field_pipeline_probe import FieldPipelineProbe, summary


def revision(owner):
    return [getattr(owner, name, 0) for name in (
        '_observation_epoch', '_observation_serial', '_answer_query_generation', '_capture_failure_revision')]


def job_state(owner):
    job = getattr(owner, '_content_revalidation_job', None)
    if job is None:
        return None
    return {'id': id(job), 'identity': list(job['identity'][:4]),
            'proof_count': len(job['identity'][4]), 'done': job['task'].done(),
            'stop': job['stop'].is_set(), 'cooperative': job['cooperative']}


def collect_probe_report(result, probes, setup_checks):
    """Keep early baseline failures too, not only successful phase setup."""
    result['setup_checks'] = setup_checks
    if probes:
        probe = probes[0]
        result['pipeline_trace'] = probe.events
        result['probe_dropped'], result['probe_errors'] = probe.dropped, probe.errors


class ReviewProbe(FieldPipelineProbe):
    def __enter__(self):
        super().__enter__()
        from plugin.plugins.hsr_companion.runtime import live_refresh
        from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore

        original_cancel = live_refresh.cancel_guard_review

        def cancel(owner):
            if owner is self.owner:
                caller = inspect.currentframe().f_back
                try:
                    self.emit('review_retirement_call', origin=caller.f_code.co_name,
                              line=caller.f_lineno, before=job_state(owner), current=revision(owner))
                finally:
                    del caller
            return original_cancel(owner)

        self.stack.enter_context(patch.object(live_refresh, 'cancel_guard_review', cancel))
        original_review = live_refresh._revalidate_guard_text

        async def review(owner, latest, coordinates, **kw):
            before = job_state(owner)
            if owner is self.owner:
                from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
                value = kw.get('evidence') or getattr(owner, '_committed_observation', None) or {}
                screen = value.get('screen_facts') or {}
                proofs = [p for f in screen.get('facts') or [] for p in f.get('evidence') or []
                          if p.get('validation_scope') != 'control_presence']
                proofs += screen.get('visible_text') or []
                proofs += [p for p in kw.get('controls') or () if
                           (p.get('control') is True or p.get('identity_literal') is True) and p.get('text')]
                keys = frozenset(proof_key(p) for p in proofs if proof_key(p)[0] in coordinates)
                job = getattr(owner, '_content_revalidation_job', None)
                target = (*revision(owner), keys)
                changed = [name for index, name in enumerate(('epoch', 'commit_serial', 'question', 'capture_failure', 'proof_set'))
                           if job is not None and job['identity'][index] != target[index]]
                self.emit('review_request', before=before, current=revision(owner),
                          region_count=len(coordinates), cooperative=kw.get('cooperative', False),
                          changed_dependencies=changed)
            try:
                result = await original_review(owner, latest, coordinates, **kw)
                if owner is self.owner:
                    self.emit('review_return', before=before, after=job_state(owner),
                              state=result.state, confirmed=len(result.confirmed_coordinates), current=revision(owner))
                return result
            except BaseException as exc:
                if owner is self.owner:
                    self.emit('review_exit', before=before, after=job_state(owner),
                              exception=type(exc).__name__, current=revision(owner))
                raise

        self.stack.enter_context(patch.object(live_refresh, '_revalidate_guard_text', review))
        original_verify = self.owner._vision.verify_text_regions

        def verify(payload, proofs, **kw):
            started, initial = time.monotonic(), revision(self.owner)
            trace = kw.get('trace') if kw.get('trace') is not None else []
            cancelled = kw.get('cancelled')
            saw_cancel = False
            self.emit('native_review_started', frame_digest=hashlib.sha256(payload).hexdigest(),
                      proof_count=len(proofs), job=job_state(self.owner), current=initial)

            def cancellation():
                nonlocal saw_cancel
                stopped = bool(cancelled and cancelled())
                if stopped and not saw_cancel:
                    saw_cancel = True
                    self.emit('native_review_cancelled', initial=initial, current=revision(self.owner),
                              job=job_state(self.owner), completed_proofs=sum(t.get('stage') == 'proof_decision' for t in trace),
                              elapsed=round(time.monotonic() - started, 3))
                return stopped

            error, confirmed = None, None
            try:
                confirmed = original_verify(payload, proofs, **{**kw, 'trace': trace, 'cancelled': cancellation})
                return confirmed
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                self.emit('native_review_finished', initial=initial, current=revision(self.owner),
                          seconds=round(time.monotonic() - started, 3), exception=error,
                          confirmed=len(confirmed) if confirmed is not None else None, trace=trace)

        self.stack.enter_context(patch.object(self.owner._vision, 'verify_text_regions', verify))
        original_commit = ObservationStore.commit

        def commit(store, analysis, **kw):
            if store.owner is self.owner:
                self.emit('commit_entered', job=job_state(self.owner), current=revision(self.owner),
                          previous=summary(getattr(self.owner, '_committed_observation', None)), incoming=summary(analysis))
            return original_commit(store, analysis, **kw)

        self.stack.enter_context(patch.object(ObservationStore, 'commit', commit))
        original_apply = self.owner._apply_local_analysis

        async def apply(analysis, **kw):
            self.emit('apply_entered', incoming=summary(analysis), job=job_state(self.owner), current=revision(self.owner))
            try:
                result = await original_apply(analysis, **kw)
                self.emit('apply_returned', committed=summary(getattr(self.owner, '_committed_observation', None)),
                          job=job_state(self.owner), current=revision(self.owner))
                return result
            except BaseException as exc:
                self.emit('apply_exit', exception=type(exc).__name__)
                raise

        self.stack.enter_context(patch.object(self.owner, '_apply_local_analysis', apply))
        return self


async def run(args):
    from plugin.plugins.hsr_companion.tests import test_resident_observer as fixtures
    original_configure = fixtures.configure_loop
    probes = []
    setup_checks = []

    with ExitStack() as stack:
        original_action, original_context = harness.ui_action, harness.ui_context

        def action(base, name):
            started = time.monotonic()
            print(json.dumps({'kind': 'environment_action_started', 'action': name}), flush=True)
            try:
                result = original_action(base, name)
            except Exception as exc:
                if name == 'stop_auto_observation' and isinstance(exc, TimeoutError):
                    # A timed-out HTTP receipt is not proof the stop failed.
                    # The unchanged helper is stopped AFTER the old native
                    # worker is drained; require both facts before proceeding.
                    from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
                    paused, helper = original_context(base), VisionAssistantClient().health()
                    check = {'observer_disabled': paused.get('observer', {}).get('enabled') is False,
                             'original_helper_disconnected': not helper.get('connected')}
                    setup_checks.append(check)
                    if all(check.values()):
                        print(json.dumps({'kind': 'pause_confirmed_after_receipt_timeout', **check}), flush=True)
                        return {'pause_confirmed_after_receipt_timeout': True}
                raise RuntimeError(name + ': ' + type(exc).__name__) from exc
            print(json.dumps({'kind': 'environment_action_completed', 'action': name,
                              'seconds': round(time.monotonic() - started, 3)}), flush=True)
            return result

        def context(base):
            started = time.monotonic()
            print(json.dumps({'kind': 'environment_context_started'}), flush=True)
            result = original_context(base)
            print(json.dumps({'kind': 'environment_context_completed',
                              'enabled': result.get('observer', {}).get('enabled'),
                              'seconds': round(time.monotonic() - started, 3)}), flush=True)
            return result

        stack.enter_context(patch.object(harness, 'ui_action', action))
        stack.enter_context(patch.object(harness, 'ui_context', context))

        def configure(owner, client):
            original_configure(owner, client)
            probe = stack.enter_context(ReviewProbe(owner, limit=10000))
            probes.append(probe)
            if getattr(args, 'preload_backend', False):
                # Prepare the engine, NOT a frame or answer. Keep cold-start
                # loading separate from the resident capture/query timeline.
                started = time.monotonic()
                backend = owner._vision._get_backend()
                probe.emit('backend_preloaded_without_frame', seconds=round(time.monotonic() - started, 6),
                           backend_class=type(backend).__name__, recognition_calls=0)

        stack.enter_context(patch.object(fixtures, 'configure_loop', configure))

        async def phases(owner, args, result, emit):
            from plugin.plugins.hsr_companion.runtime.build_info import BUILD_REVISION
            from plugin.sdk.plugin import NekoPluginBase
            raw = getattr(owner, '_committed_observation', None) or {}
            if raw.get('page', {}).get('page_type') != 'exploration_hud':
                raise RuntimeError('Exploration baseline absent; transition not counted')
            signals = Path(args.signals).resolve()
            root = Path(__file__).resolve().parents[1]
            if not signals.is_relative_to(root / 'dist') or signals.exists():
                raise ValueError('Use a new plugin-owned signal directory')
            signals.mkdir()
            probe = probes[0]
            result['pipeline_trace'] = probe.events
            result['setup_checks'] = setup_checks
            result['build_revision'] = BUILD_REVISION
            on_controls = getattr(args, 'on_control_change', False)
            automatic = args.on_page_discovery or on_controls
            result['trigger'] = 'first_exploration_controls_change' if on_controls else 'first_character_preview' if args.on_page_discovery else 'human_reply'
            result['recognizer_class'] = type(owner._vision).__name__
            events, submissions = [], []
            owner.ctx = SimpleNamespace(bus=NekoPluginBase(SimpleNamespace(plugin_id='isolated-character-trace',
                metadata={}, bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: list(events))))).bus)
            owner._target_character = lambda: 'hsr_isolated_test'
            owner.push_message = lambda **kw: submissions.append(kw) or {'submitted': True}
            discovered = asyncio.Event()
            publisher = owner._publish_scene_preview

            async def preview(value):
                await publisher(value)
                if (value.get('page') or {}).get('page_type') == 'character_detail' and not discovered.is_set():
                    self_time = time.time()
                    result['character_discovered_at'] = self_time
                    probe.emit('first_character_preview', current=revision(owner),
                               committed=summary(getattr(owner, '_committed_observation', None)))
                    discovered.set()

            if args.on_page_discovery:
                stack.enter_context(patch.object(owner, '_publish_scene_preview', preview))
            monitor = None
            if on_controls:
                from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, regions_changed
                controls = list(getattr(owner, '_accepted_frame_page_regions', ()))
                initial = await asyncio.to_thread(owner._assistant_client.fetch_frame, 0)
                if initial is None or not controls:
                    raise RuntimeError('Fresh exploration controls absent; early trigger not armed')
                before = await asyncio.to_thread(region_stamp, initial.image_bytes, controls)

                async def watch_controls():
                    # Scheduling stimulus only. Pixel change is NOT admitted as
                    # a new page or new facts; the real tool must prove those.
                    while not discovered.is_set():
                        frame = await asyncio.to_thread(owner._assistant_client.fetch_frame, 0)
                        if frame is not None:
                            now = await asyncio.to_thread(region_stamp, frame.image_bytes, controls)
                            if regions_changed(before, now):
                                probe.emit('early_question_trigger', frame_seq=frame.frame_seq,
                                    captured_at=frame.timestamp, payload_sha256=hashlib.sha256(frame.image_bytes).hexdigest(),
                                    basis='exploration_control_pixels_only_not_page_assertion')
                                discovered.set()
                                return
                        await asyncio.sleep(.25)
                monitor = asyncio.create_task(watch_controls())
                stack.callback(monitor.cancel)
            emit('awaiting_character_page', marker=str(signals / 'character'))
            until = time.monotonic() + 300
            while not (discovered.is_set() if automatic else (signals / 'character').exists()):
                if time.monotonic() >= until:
                    raise TimeoutError('Human transition not received')
                await asyncio.sleep(.25)
            probe.phase = 'character_request'
            started = time.monotonic()
            packet = await owner.hsr_companion_prepare(query=getattr(args, 'question', '真珠这面板怎么样？接下来怎么养？'), intent='assess_panel',
                consultation={'scope': 'current_panel', 'subject_state': 'specified', 'character': '真珠'})
            result['formal_results'].append(packet)
            emit('formal_character_result', seconds=round(time.monotonic() - started, 3),
                 state=packet.get('query_answer', {}).get('state'),
                 assessment=packet.get('guidance_answer', {}).get('assessment', {}).get('state'),
                 timing=packet.get('query_timing'))
            pending = getattr(owner, '_pending_answer', None)
            if pending is not None:
                deadline = pending.expires_at
                events.append({'type': 'user_message', 'lanlan': 'hsr_isolated_test', 'content': '好', '_ts': time.time()})
                confirmation = await owner.hsr_companion_prepare(query='好', pending_reply={
                    'request_id': pending.request_id, 'relation': 'confirm', 'player_message': '好'})
                emit('local_confirmation', accepted=confirmation['pending_reply']['accepted'],
                     deadline_unchanged=pending.expires_at == deadline)
                task = getattr(owner, '_followup_task', None)
                if task is not None:
                    await task
            result['followup_status'] = getattr(owner, '_followup_status', {})
            result['local_submissions'] = submissions
            result['final_committed'] = summary(getattr(owner, '_committed_observation', None))
            result['probe_dropped'], result['probe_errors'] = probe.dropped, probe.errors
            emit('character_trace_complete', followup=result['followup_status'],
                 events=len(probe.events), dropped=probe.dropped, errors=probe.errors)

        stack.enter_context(patch.object(harness, 'delivery_phases', getattr(args, 'delivery_phases', phases)))
        args.host = 'http://127.0.0.1:48916'
        args.pending_protocol = args.current_item = args.three_fields_signals = args.three_fields_current = False
        args.collect_diagnostic_report = lambda result: collect_probe_report(result, probes, setup_checks)
        await harness.verify(args)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--signals', required=True)
    parser.add_argument('--port', type=int, default=8455)
    parser.add_argument('--on-page-discovery', action='store_true')
    asyncio.run(run(parser.parse_args()))
