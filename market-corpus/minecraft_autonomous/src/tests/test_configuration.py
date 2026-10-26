def test_backend_compatibility_wrapper_uses_canonical_installer(plugin_module):
    import importlib

    from agent_mc_autonomy.core_distribution.install import SOURCE, verify_runtime
    adapter = importlib.import_module(plugin_module.__name__ + ".backend.install")
    assert adapter.verify_runtime is verify_runtime
    assert adapter.SOURCE == SOURCE
    assert SOURCE["external_viewer"]["source_revision"] == "8b89927db49c9547a50029f039cbfe534d38e78b"
    assert SOURCE["version"] == "0.5.7-rc.99"
    assert SOURCE["source_revision"] == "9b93af4523ddb0556c89a2a5edc9a0e8e2c0f05e"
    assert SOURCE["sha256"] == "9aedb3e1280a48ca68a3f4bdc9f6403b2f38b2ea42ee497c1624773de4c0695f"


def test_canonical_distribution_version():
    from importlib.metadata import version

    assert version("agent-mc-autonomy") == "0.2.103"
