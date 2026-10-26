"""Bounded reconnect policy shared by the session and renderer snapshot."""

LEASE_SECONDS = 30
CONNECTION_POLICY = {
    'poll_timeout_ms': 6000,
    'reconnect_grace_ms': 20000,
    'active_poll_ms': 1500,
    'idle_poll_ms': 3000,
    'lease_seconds': LEASE_SECONDS,
}
