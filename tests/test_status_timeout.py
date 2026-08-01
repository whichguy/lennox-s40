"""status may print JSON and still exit EX_TIMEOUT when not ready."""
from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import lennox_s40 as m


def test_status_not_ready_json_and_rc5(monkeypatch, capsys):
    z = SimpleNamespace(
        id=0,
        name="Z",
        temperature=70,
        humidity=40,
        systemMode="cool",
        hsp=65,
        csp=74,
        sp=70,
        fanMode="auto",
        humidityMode="off",
        scheduleHold=False,
    )
    system = SimpleNamespace(
        zone_list=[z],
        name="Home",
        serialNumber=None,
        productType=None,
        single_setpoint_mode=False,
        manualAwayMode=False,
    )

    @asynccontextmanager
    async def fake_session(args):
        yield None, system, {}, False  # not ready

    monkeypatch.setattr(m, "session", fake_session)
    args = argparse.Namespace(
        full=False,
        cmd="status",
        ip=None,
        app_id=None,
        no_rediscover=True,
        lan_scan=False,
        first_zone=False,
        zone=None,
    )
    rc = asyncio.run(m.cmd_status(args))
    assert rc == m.EX_TIMEOUT
    out = json.loads(capsys.readouterr().out)
    assert out.get("ready") is False
