"""Unit tests: config load/save/lock."""
from __future__ import annotations

import json
import os
import concurrent.futures

import lennox_s40 as m


def test_load_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LENNOX_CONFIG", str(tmp_path / "missing.json"))
    cfg = m.load_config()
    assert cfg["version"] == m.CONFIG_VERSION


def test_load_corrupt(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("LENNOX_CONFIG", str(p))
    cfg = m.load_config()
    assert cfg["version"] == m.CONFIG_VERSION


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
