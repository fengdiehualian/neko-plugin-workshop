"""Compatibility launcher; the standalone runtime belongs to minecraft-ai-friend."""
from agent_mc_autonomy.cli import main as framework_main


def main(argv=None):
    from backend.install import verify_runtime

    return framework_main(argv, entry_resolver=verify_runtime)


if __name__ == "__main__":
    raise SystemExit(main())
