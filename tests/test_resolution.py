"""Unit tests: resolve helpers (no network)."""
from __future__ import annotations

import argparse

import pytest

import lennox_s40 as m


def test_is_ip():
    assert m._is_ip("192.168.1.1")
    assert not m._is_ip("192.168.1")
    assert not m._is_ip("host.local")


def test_env_int_bad(monkeypatch):
    monkeypatch.setenv("LENNOX_TIMEOUT", "abc")
    assert m._env_int("LENNOX_TIMEOUT", 90) == 90


def test_generate_app_id_unique():
    a = m.generate_app_id()
    b = m.generate_app_id()
    assert a.startswith("mapp")
    assert a != b
    assert len(a) == 4 + 24


def test_effective_app_id_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    m.save_config({"app_id": "mappCONFIG000000000000001"})
    cfg = m.load_config()
    args = argparse.Namespace(app_id="mappCLI000000000000000001")
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    assert m.effective_app_id(args, cfg) == "mappCLI000000000000000001"

    args = argparse.Namespace(app_id=None)
    monkeypatch.setenv("LENNOX_APP_ID", "mappENV0000000000000000001")
    assert m.effective_app_id(args, cfg) == "mappENV0000000000000000001"

    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    assert m.effective_app_id(args, cfg) == "mappCONFIG000000000000001"


def test_migrate_legacy_app_id(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    monkeypatch.delenv("LENNOX_APP_ID", raising=False)
    m.save_config({"app_id": m.LEGACY_SHARED_APP_ID})
    cfg = m.load_config()
    args = argparse.Namespace(app_id=None)
    new_id = m.effective_app_id(args, cfg)
    assert new_id != m.LEGACY_SHARED_APP_ID
    assert m.load_config()["app_id"] == new_id


def test_mode_map():
    assert m.MODE_MAP["auto"] == "heat and cool"
    assert m.MODE_MAP["cool"] == "cool"


def test_finite_temp():
    assert m._finite_temp(72, "t") == 72
    with pytest.raises(m.CliError) as e:
        m._finite_temp(float("nan"), "t")
    assert e.value.code == m.EX_BAD_REQ
    with pytest.raises(m.CliError):
        m._finite_temp(120, "t")


def test_remember_identity_clears_host(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "c.json"))
    m.save_config({"host": "old.local", "ip": "1.2.3.4"})
    cfg = m.load_config()
    cfg = m.remember_identity(cfg, ip="5.6.7.8", app_id="mappX", clear_host=True)
    assert "host" not in cfg or cfg.get("host") in (None, "")
    assert cfg["ip"] == "5.6.7.8"
