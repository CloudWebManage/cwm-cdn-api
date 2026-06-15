import importlib
import json
import os
import sys


def load_vector_module(monkeypatch, **env):
    for key in ["ENABLE_ES_SINK", "ENABLE_DEBUG_SINK", "ENABLE_PLATFORM_LOGS"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", "tenant-nginx"))
    module = importlib.import_module("render_vector_config")
    importlib.reload(module)
    sys.path.pop()
    return module


def test_platform_logs_sink_is_independent_from_tenant_es(monkeypatch, capsys):
    module = load_vector_module(monkeypatch, ENABLE_PLATFORM_LOGS="true")
    module.main()
    out = capsys.readouterr().out
    sinks = json.loads(out.split("sinks: ", 1)[1])
    assert sinks["platform_console"]["inputs"] == ["parse_nginx"]
    assert sinks["platform_console"]["encoding"] == {"codec": "json"}
    assert "debug" not in sinks


def test_debug_sink_is_default_when_no_other_sink_enabled(monkeypatch, capsys):
    module = load_vector_module(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    sinks = json.loads(out.split("sinks: ", 1)[1])
    assert list(sinks) == ["debug"]
