# -*- coding: utf-8 -*-
"""pytest 的公共夹具：把本仓三个脚本按路径导进来，不依赖安装态。"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    path = os.path.join(ROOT, *rel.split("/"))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def roster():
    return _load("forge_roster", "scripts/generate_roster_tables.py")


@pytest.fixture(scope="session")
def gate():
    mod = _load("forge_gate", "skills/plugin-forge/scripts/check_contract_approval.py")
    return mod


@pytest.fixture(scope="session")
def dry_load():
    sys.path.insert(0, os.path.join(ROOT, "skills", "neko-plugin-sandbox", "scripts"))
    return _load("forge_dry_load",
                 "skills/neko-plugin-sandbox/scripts/dry_load.py")


@pytest.fixture
def repo_root():
    return ROOT
