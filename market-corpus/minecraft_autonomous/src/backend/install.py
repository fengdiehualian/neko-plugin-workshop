"""Compatibility wrapper; implementation belongs to minecraft-ai-friend."""

from agent_mc_autonomy.core_distribution.install import SOURCE as SOURCE
from agent_mc_autonomy.core_distribution.install import SOURCE_DIR as SOURCE_DIR
from agent_mc_autonomy.core_distribution.install import main
from agent_mc_autonomy.core_distribution.install import prepare as prepare
from agent_mc_autonomy.core_distribution.install import runtime_entry as runtime_entry
from agent_mc_autonomy.core_distribution.install import verify_archive as verify_archive
from agent_mc_autonomy.core_distribution.install import verify_runtime as verify_runtime

if __name__ == "__main__":
    main()
