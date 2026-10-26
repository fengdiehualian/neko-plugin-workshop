"""Changing challenged pixels is not a new question or a new immutable fact.

Synthetic checkpoint timing reproduces the live 9->10->11 proof-set churn;
does not claim OCR accuracy or a real cat response.
"""
import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime.live_refresh import _revalidate_guard_text, cancel_guard_review
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture

A = {'text': '143', 'region': {'left': .1, 'top': .1, 'right': .2, 'bottom': .2}}
B = {'text': '3335', 'region': {'left': .3, 'top': .3, 'right': .4, 'bottom': .4}}


def evidence(proofs):
    return {'screen_facts': {'facts': [{'kind': 'stat', 'scope': 'attributes', 'value': p['text'], 'evidence': [p]}
                                     for p in proofs], 'visible_text': []}}


def setup():
    queued, calls = [], []

    def enqueue(callback):
        future = Future()
        queued.append((future, callback))
        return future

    def verify(payload, proofs, **kw):
        assert not kw['cancelled']()
        calls.append(proofs)
        return {proof_key(p) for p in proofs}

    owner = SimpleNamespace(_observation_epoch=1, _observation_serial=1, _answer_query_generation=1,
        _capture_failure_revision=0, _observer_status={'enabled': True},
        _vision=SimpleNamespace(enqueue_review=enqueue, verify_text_regions=verify),
        _assistant_client=SimpleNamespace(fetch_frame=lambda seq: frame(picture())))
    return owner, queued, calls


@pytest.mark.parametrize('first,next_proofs', [([A], [A, B]), ([A, B], [A])])
def test_scope_change_keeps_queued_work_and_receipt_has_exact_current_literals(first, next_proofs):
    async def run():
        owner, queued, calls = setup()
        initial = await _revalidate_guard_text(owner, frame(picture()), {proof_key(p)[0] for p in first},
            evidence=evidence([A, B]), cooperative=True)
        assert initial.state == 'busy'
        future, callback = queued[0]
        next_read = asyncio.create_task(_revalidate_guard_text(owner, frame(picture()),
            {proof_key(p)[0] for p in next_proofs}, evidence=evidence(next_proofs), cooperative=True))
        try:
            await asyncio.sleep(.02)
            assert not future.cancelled(), 'Same-page proof-set churn cancelled the queued review'
            future.set_result(callback())
            receipt = await next_read
            assert len(queued) == len(calls) == 1
            expected = {proof_key(p)[0] for p in first if p in next_proofs}
            assert receipt.confirmed_coordinates == expected
            assert receipt.capture is not None
            # An added region is NOT certified just because the old batch finished.
            if len(next_proofs) > len(first):
                assert proof_key(B)[0] not in receipt.confirmed_coordinates
                additional = asyncio.create_task(_revalidate_guard_text(owner, frame(picture()), {proof_key(B)[0]},
                    evidence=evidence(next_proofs), cooperative=True))
                await asyncio.sleep(.02)
                queued[1][0].set_result(queued[1][1]())
                assert (await additional).confirmed_coordinates == {proof_key(B)[0]}
                assert len(calls) == 2  # Progress, without claiming unexecuted work.
        finally:
            cancel_guard_review(owner)
            next_read.cancel()
            await asyncio.gather(next_read, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize('field', ['_observation_serial', '_capture_failure_revision'])
def test_new_commit_or_capture_failure_still_retires_queued_work(field):
    async def run():
        owner, queued, calls = setup()
        await _revalidate_guard_text(owner, frame(picture()), {proof_key(A)[0]},
            evidence=evidence([A]), cooperative=True)
        setattr(owner, field, getattr(owner, field) + 1)
        receipt = await _revalidate_guard_text(owner, frame(picture()), {proof_key(A)[0]},
            evidence=evidence([A]), cooperative=True)
        assert queued[0][0].cancelled()
        assert not receipt
        assert not receipt.confirmed_coordinates
        assert not calls
        cancel_guard_review(owner)

    asyncio.run(run())


def test_old_literal_cannot_prove_a_different_number_at_the_same_coordinates():
    async def run():
        owner, queued, _ = setup()
        await _revalidate_guard_text(owner, frame(picture()), {proof_key(A)[0]},
            evidence=evidence([A]), cooperative=True)
        future, callback = queued[0]
        changed = {**A, 'text': '144'}
        next_read = asyncio.create_task(_revalidate_guard_text(owner, frame(picture()), {proof_key(A)[0]},
            evidence=evidence([changed]), cooperative=True))
        try:
            await asyncio.sleep(.02)
            if not future.cancelled():
                future.set_result(callback())
            receipt = await next_read
            assert not receipt
            assert not receipt.confirmed_coordinates
        finally:
            cancel_guard_review(owner)
            next_read.cancel()
            await asyncio.gather(next_read, return_exceptions=True)

    asyncio.run(run())
