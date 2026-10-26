# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Implementation modules for the ``neko_stellaris`` plugin.

Kept as a package rather than a flat pile of modules so the plugin's entry point
(:mod:`plugin.plugins.neko_stellaris`) can stay readable and the Stellaris-facing
file formats stay isolated in :mod:`.voicepack`.
"""

from __future__ import annotations

__all__ = [
    "audiowav",
    "ddsicon",
    "nekoconfig",
    "tts",
    "voicepack",
]
