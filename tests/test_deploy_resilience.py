from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def test_every_container_has_bounded_log_rotation():
    path = ROOT / "compose.yml"
    if not path.is_file():
        pytest.skip("server deployment files are excluded from the public Worktrail tree")
    compose = path.read_text(encoding="utf-8")
    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert compose.count("logging: *bounded-logging") == 4


def test_public_server_cannot_disable_operator_access_checks(monkeypatch):
    import main

    monkeypatch.delenv("CASEBOOK_ENFORCE_ACCESS", raising=False)
    assert main.access_enforcement("0.0.0.0") is True
    monkeypatch.setenv("CASEBOOK_ENFORCE_ACCESS", "0")
    assert main.access_enforcement("0.0.0.0") is True
    assert main.access_enforcement("127.0.0.1") is False
