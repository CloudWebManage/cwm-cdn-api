import os
import sys
import tempfile
import importlib

import pytest


@pytest.fixture
def tenant_nginx_entrypoint():
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", "tenant-nginx"))
    entrypoint = importlib.import_module("entrypoint")
    importlib.reload(entrypoint)
    sys.path.pop()
    yield entrypoint


@pytest.fixture
def cache_nginx_entrypoint():
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", "cache-nginx"))
    entrypoint = importlib.import_module("entrypoint")
    importlib.reload(entrypoint)
    sys.path.pop()
    yield entrypoint


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as td:
        yield td
