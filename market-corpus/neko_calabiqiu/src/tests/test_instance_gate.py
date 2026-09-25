from neko_calabiqiu.core.instance_gate import release, try_claim


def test_instance_gate_blocks_replacement_until_owner_releases():
    key = f"instance-gate-test-{id(object())}"
    owner = object()
    replacement = object()

    assert try_claim(key, owner)
    assert not try_claim(key.upper(), owner)
    assert not try_claim(key, replacement)

    release(key, replacement)
    assert not try_claim(key, replacement)

    release(key, owner)
    assert try_claim(key, replacement)
    release(key, replacement)
