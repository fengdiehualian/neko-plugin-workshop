"""Offline worker package for kards_companion.

This package is deliberately stdlib-only at import time.  It must never import
the N.E.K.O host SDK, never push messages to the host, never download anything,
and never create bundle directories.
"""
