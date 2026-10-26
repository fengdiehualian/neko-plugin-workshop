# Body distribution compatibility

The canonical build entry, hash verification and dependency manifest live in
minecraft-ai-friend/packages/agent-mc-autonomy/src/agent_mc_autonomy/core_distribution.
This directory only forwards the existing build.py, install.py and check_disconnected.py commands.
The plugin dependency pins the actual implementation. After SDK dependency sync, read `vendor/agent_mc_autonomy/core_distribution/source.json`
for the exact current core source, archive, build/type receipts and hashes.
Follow the [source installation guide](../docs/install-source.md#3-准备精确后端);
no unpublished companion ZIP or guessed release is required.
Changing the pin does not replace a running body; install explicitly into a new directory.

Canonical build/type receipt hashes and the archive hash are verified during installation.
No Neko runtime is needed for this check. LICENSE-Agent-MC-World.txt preserves the body source license.
