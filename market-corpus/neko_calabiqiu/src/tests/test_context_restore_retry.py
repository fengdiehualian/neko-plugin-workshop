from __future__ import annotations

from neko_calabiqiu.core.context_restore_retry import ContextRestoreRetry


def test_context_restore_retry_uses_bounded_exponential_backoff():
    retry = ContextRestoreRetry(max_backoff_seconds=4.0)
    retry.request()

    assert retry.begin_attempt(100.0)
    assert not retry.begin_attempt(100.0)
    retry.finish_attempt(success=False, now=100.0)
    assert retry.attempts == 1
    assert retry.next_attempt_at == 101.0
    assert not retry.begin_attempt(100.9)

    assert retry.begin_attempt(101.0)
    retry.finish_attempt(success=False, now=101.0)
    assert retry.next_attempt_at == 103.0

    assert retry.begin_attempt(103.0)
    retry.finish_attempt(success=False, now=103.0)
    assert retry.next_attempt_at == 107.0

    assert retry.begin_attempt(107.0)
    retry.finish_attempt(success=False, now=107.0)
    assert retry.next_attempt_at == 111.0


def test_context_restore_retry_clears_only_after_success():
    retry = ContextRestoreRetry()
    retry.request()
    assert retry.begin_attempt(10.0)
    retry.finish_attempt(success=False, now=10.0)
    assert retry.pending

    assert retry.begin_attempt(11.0)
    retry.finish_attempt(success=True, now=11.0)

    assert not retry.pending
    assert retry.attempts == 0
    assert retry.next_attempt_at == 0.0
    assert not retry.in_flight


def test_shutdown_attempt_can_ignore_normal_backoff_without_overlapping():
    retry = ContextRestoreRetry()
    retry.request()
    assert retry.begin_attempt(10.0)
    retry.finish_attempt(success=False, now=10.0)

    assert not retry.begin_attempt(10.1)
    assert retry.begin_attempt(10.1, ignore_backoff=True)
    assert not retry.begin_attempt(10.1, ignore_backoff=True)
