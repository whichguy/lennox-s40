"""Unit tests: config load/save/lock."""
from __future__ import annotations

import concurrent.futures
import json

import lennox_s40 as m
import pytest


def test_load_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "missing.json"))
    cfg = m.load_config()
    assert cfg["version"] == m.CONFIG_VERSION


def test_load_corrupt_fail_closed_preserves_file(tmp_path, monkeypatch, capsys):
    """Malformed existing config → EX_BAD_REQ (3); bytes left untouched; path on stderr."""
    p = tmp_path / "c.json"
    payload = b"{not json"
    p.write_bytes(payload)
    monkeypatch.setenv("LENNOX_CONFIG", str(p))
    with pytest.raises(m.CliError) as e:
        m.load_config()
    assert e.value.code == m.EX_BAD_REQ
    assert p.read_bytes() == payload
    assert str(p) in e.value.message
    err = capsys.readouterr().err
    assert str(p) in err


def test_load_corrupt_soft_reset_opt_in(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("LENNOX_CONFIG", str(p))
    cfg = m.load_config(fail_closed=False)
    assert cfg["version"] == m.CONFIG_VERSION
    # soft path still preserves on-disk content
    assert p.read_text(encoding="utf-8") == "{not json"


def test_load_non_object_fail_closed(tmp_path, monkeypatch, capsys):
    p = tmp_path / "c.json"
    p.write_text("[1, 2, 3]\n", encoding="utf-8")
    before = p.read_bytes()
    monkeypatch.setenv("LENNOX_CONFIG", str(p))
    with pytest.raises(m.CliError) as e:
        m.load_config()
    assert e.value.code == m.EX_BAD_REQ
    assert p.read_bytes() == before
    assert str(p) in e.value.message
    assert str(p) in capsys.readouterr().err


def test_config_show_malformed_cli_rc3(tmp_path, monkeypatch, capsys):
    p = tmp_path / "c.json"
    p.write_text("{bad", encoding="utf-8")
    before = p.read_bytes()
    monkeypatch.setenv("LENNOX_CONFIG", str(p))
    rc = m.main(["config", "show"])
    assert rc == m.EX_BAD_REQ
    assert p.read_bytes() == before
    err = capsys.readouterr().err
    assert str(p) in err


def test_save_roundtrip_mode(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    monkeypatch.setenv("LENNOX_CONFIG", str(p))
    m.save_config({"ip": "203.0.113.9", "app_id": "mapp123"})
    assert p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"
    cfg = m.load_config()
    assert cfg["ip"] == "203.0.113.9"
    assert cfg["app_id"] == "mapp123"
    assert "updated_at" in cfg


def test_concurrent_saves(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    monkeypatch.setenv("LENNOX_CONFIG", str(p))

    def writer(i: int) -> None:
        m.save_config({"ip": f"203.0.113.{i % 200}", "n": i})

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(writer, range(40)))
    cfg = m.load_config()
    assert "ip" in cfg
    # file remains valid JSON
    json.loads(p.read_text(encoding="utf-8"))
