#!/usr/bin/env python3
"""Local-only CLI for Lennox S40 (and S30/E30 LAN) thermostats.

Uses the reverse-engineered lennoxs30api library over HTTPS on the LAN.
No cloud credentials. No password. Self-signed TLS is expected.

Running config (persisted JSON):
  $LENNOX_CONFIG  or  $XDG_CONFIG_HOME/lennox-s40/config.json
  or  ~/.config/lennox-s40/config.json

  Fields: ip, host, app_id, serial, last_ok_at, updated_at

Resolution order for address:
  1. --ip (explicit)
  2. LENNOX_IP env
  3. config ip/host
  4. mDNS + Connect probe (discover)

If the chosen address fails Connect, rediscover automatically (unless
--no-rediscover), write the new address to config, and retry once.

Env:
  LENNOX_IP, LENNOX_APP_ID, LENNOX_TIMEOUT, LENNOX_CONFIG, LENNOX_POLL_MAX

Commands:
  status | mode | cool | heat | fan | away | discover | config
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

try:
    from lennoxs30api.s30api_async import s30api_async
except ImportError:
    print(
        "lennoxs30api not installed. Run: bash scripts/lennox-s40 --setup\n"
        "or: pip install lennoxs30api aiohttp pytz",
        file=sys.stderr,
    )
    sys.exit(2)

ENV_IP = os.environ.get("LENNOX_IP", "").strip()
ENV_APP_ID = os.environ.get("LENNOX_APP_ID", "").strip()
DEFAULT_APP_ID = "mapp079372367644467046827200"
DEFAULT_TIMEOUT = int(os.environ.get("LENNOX_TIMEOUT", "90"))
POLL_MAX = int(os.environ.get("LENNOX_POLL_MAX", "80"))
PROBE_TIMEOUT = float(os.environ.get("LENNOX_PROBE_TIMEOUT", "4"))
# Set LENNOX_NO_LAN_SCAN=1 to skip /24 Connect sweep (tests / slow networks)
ENV_NO_LAN_SCAN = os.environ.get("LENNOX_NO_LAN_SCAN", "").strip() in {"1", "true", "yes"}

CONFIG_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def config_path() -> str:
    if os.environ.get("LENNOX_CONFIG"):
        return os.path.expanduser(os.environ["LENNOX_CONFIG"])
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg, "lennox-s40", "config.json")


def load_config() -> dict[str, Any]:
    path = config_path()
    if not os.path.isfile(path):
        return {"version": CONFIG_VERSION}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": CONFIG_VERSION}
        data.setdefault("version", CONFIG_VERSION)
        return data
    except (OSError, json.JSONDecodeError) as e:
        print(f"lennox-s40: warning: bad config {path}: {e}", file=sys.stderr)
        return {"version": CONFIG_VERSION}


def save_config(cfg: dict[str, Any]) -> None:
    path = config_path()
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    cfg = dict(cfg)
    cfg["version"] = CONFIG_VERSION
    cfg["updated_at"] = _utc_now()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def resolve_host(host: str) -> str:
    """Return dotted IP; leave already-IP strings alone."""
    host = host.strip()
    if not host:
        raise SystemExit("empty host")
    if all(p.isdigit() for p in host.split(".")) and host.count(".") == 3:
        return host
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as e:
        raise SystemExit(f"Cannot resolve host {host!r}: {e}") from e


def probe_connect(ip: str, app_id: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """Fast HTTPS Connect check (self-signed OK)."""
    url = f"https://{ip}/Endpoints/{app_id}/Connect"
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        return e.code in (200, 204)
    except Exception:
        return False


def _mdns_s40_hosts(seconds: float = 5.0) -> list[str]:
    """Browse mDNS; dns-sd streams forever so we sample then kill."""
    hosts: list[str] = []
    try:
        proc = subprocess.Popen(
            ["dns-sd", "-Z", "_http._tcp", "local."],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return hosts
    try:
        # sample for a few seconds
        try:
            out, _ = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        out = out or ""
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return hosts
    for m in re.finditer(r"Lennox-S40-[A-Za-z0-9]+\.local", out, re.I):
        h = m.group(0)
        if h not in hosts:
            hosts.append(h)
    return hosts


def _local_ipv4_prefixes() -> list[str]:
    """Best-effort primary interface /24 prefixes for LAN scan fallback."""
    prefixes: list[str] = []
    try:
        # UDP trick: no packets sent
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if _is_ip(ip) and not ip.startswith("127."):
            parts = ip.split(".")
            prefixes.append(".".join(parts[:3]) + ".")
    except OSError:
        pass
    return prefixes


def _scan_lan_lennox(app_id: str, prefixes: Optional[list[str]] = None) -> list[tuple[str, str]]:
    """Parallel Connect probe of local /24s — last-resort discovery."""
    import concurrent.futures

    prefixes = prefixes or _local_ipv4_prefixes()
    if not prefixes:
        return []
    targets: list[str] = []
    for pref in prefixes:
        for i in range(1, 255):
            targets.append(f"{pref}{i}")

    found: list[tuple[str, str]] = []

    def check(ip: str) -> Optional[str]:
        return ip if probe_connect(ip, app_id, timeout=1.5) else None

    # bounded workers so we don't thrash
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        futs = {ex.submit(check, ip): ip for ip in targets}
        for fut in concurrent.futures.as_completed(futs):
            ip = fut.result()
            if ip:
                found.append((f"lan-scan:{ip}", ip))
    return found


def discover_candidates(
    *,
    prefer: Optional[str] = None,
    cfg: Optional[dict[str, Any]] = None,
    app_id: str = DEFAULT_APP_ID,
    allow_lan_scan: bool = True,
) -> list[tuple[str, str]]:
    """Return ordered list of (label, resolved_ip) candidates."""
    cfg = cfg or {}
    labels: list[str] = []
    if prefer:
        labels.append(prefer)
    if ENV_IP and ENV_IP not in labels:
        labels.append(ENV_IP)
    for key in ("ip", "host"):
        v = (cfg.get(key) or "").strip()
        if v and v not in labels:
            labels.append(v)
    serial = (cfg.get("serial") or "").strip()
    if serial:
        mdns = f"Lennox-S40-{serial}.local"
        if mdns not in labels:
            labels.append(mdns)
    for h in _mdns_s40_hosts():
        if h not in labels:
            labels.append(h)

    out: list[tuple[str, str]] = []
    seen_ip: set[str] = set()
    for lab in labels:
        try:
            ip = resolve_host(lab)
        except SystemExit:
            continue
        if ip in seen_ip:
            continue
        seen_ip.add(ip)
        out.append((lab, ip))

    # If nothing resolved (or only dead IPs will fail later), add LAN scan results
    if allow_lan_scan and not out:
        for lab, ip in _scan_lan_lennox(app_id):
            if ip not in seen_ip:
                seen_ip.add(ip)
                out.append((lab, ip))
    return out


def discover_live(
    app_id: str,
    *,
    prefer: Optional[str] = None,
    cfg: Optional[dict[str, Any]] = None,
    quiet: bool = False,
    allow_lan_scan: bool = True,
) -> Optional[dict[str, str]]:
    """Probe candidates; return {ip, host_label} for first Connect success."""
    if ENV_NO_LAN_SCAN:
        allow_lan_scan = False
    candidates = discover_candidates(
        prefer=prefer, cfg=cfg, app_id=app_id, allow_lan_scan=False
    )
    # Always try named candidates first; if all fail, optional /24 scan
    for label, ip in candidates:
        ok = probe_connect(ip, app_id)
        if not quiet:
            print(f"  probe {label} -> {ip}: {'OK' if ok else 'fail'}", file=sys.stderr)
        if ok:
            return {"ip": ip, "host": label if not _is_ip(label) and not label.startswith("lan-scan:") else ""}

    if allow_lan_scan:
        if not quiet:
            print("  named candidates failed; scanning local /24 for S40 API…", file=sys.stderr)
        for label, ip in _scan_lan_lennox(app_id):
            # scan already probed Connect; trust result
            if not quiet:
                print(f"  probe {label} -> {ip}: OK", file=sys.stderr)
            return {"ip": ip, "host": ""}

    if not quiet and not candidates:
        print(
            "No discovery candidates (mDNS empty, no config host; LAN scan found none).",
            file=sys.stderr,
        )
    return None


def _is_ip(s: str) -> bool:
    return all(p.isdigit() for p in s.split(".")) and s.count(".") == 3


def effective_app_id(args, cfg: dict[str, Any]) -> str:
    # CLI --app-id if not the library default and user overrode via env or flag
    # argparse always passes something; prefer: explicit env > config > flag default
    if ENV_APP_ID:
        return ENV_APP_ID
    if cfg.get("app_id"):
        return str(cfg["app_id"])
    return getattr(args, "app_id", None) or DEFAULT_APP_ID


def remember_success(
    cfg: dict[str, Any],
    *,
    ip: str,
    app_id: str,
    host: str = "",
    serial: Optional[str] = None,
) -> dict[str, Any]:
    cfg = dict(cfg)
    cfg["ip"] = ip
    if host:
        cfg["host"] = host
    cfg["app_id"] = app_id
    if serial:
        cfg["serial"] = serial
    cfg["last_ok_at"] = _utc_now()
    save_config(cfg)
    return cfg


def resolve_target(args) -> tuple[str, str, dict[str, Any]]:
    """
    Resolve (ip, app_id, cfg) with probe + auto rediscover.

    Order: --ip → LENNOX_IP → config → discover.
    If first choice fails Connect and rediscover allowed, scan and update config.
    """
    cfg = load_config()
    app_id = effective_app_id(args, cfg)
    explicit_ip = (getattr(args, "ip", None) or "").strip() or None
    # argparse may set ip from None; treat empty as unset
    rediscover = not getattr(args, "no_rediscover", False)
    allow_lan = not getattr(args, "no_lan_scan", False) and not ENV_NO_LAN_SCAN

    primary_label: Optional[str] = None
    if explicit_ip:
        primary_label = explicit_ip
    elif ENV_IP:
        primary_label = ENV_IP
    elif (cfg.get("ip") or "").strip():
        primary_label = str(cfg["ip"]).strip()
    elif (cfg.get("host") or "").strip():
        primary_label = str(cfg["host"]).strip()

    if primary_label:
        try:
            ip = resolve_host(primary_label)
        except SystemExit as e:
            if not rediscover:
                raise
            print(f"lennox-s40: resolve failed ({e}); rediscovering…", file=sys.stderr)
            ip = ""
        else:
            if probe_connect(ip, app_id):
                host = primary_label if not _is_ip(primary_label) else (cfg.get("host") or "")
                cfg = remember_success(cfg, ip=ip, app_id=app_id, host=host or "")
                return ip, app_id, cfg
            if not rediscover:
                raise SystemExit(
                    f"Connect failed for {ip} (app_id={app_id}). "
                    "Fix LENNOX_IP / --ip or omit --no-rediscover."
                )
            print(
                f"lennox-s40: Connect failed for {ip}; rediscovering…",
                file=sys.stderr,
            )
    elif not rediscover:
        # No address known and auto-discover disabled — fail closed (hermetic / offline).
        raise SystemExit(
            "No thermostat address. Set --ip, LENNOX_IP, run: lennox-s40 discover\n"
            f"(or omit --no-rediscover to scan LAN)\nConfig path: {config_path()}"
        )

    # Full discover (no primary with rediscover allowed, or primary failed + rediscover)
    found = discover_live(
        app_id,
        prefer=primary_label,
        cfg=cfg,
        quiet=False,
        allow_lan_scan=allow_lan,
    )
    if not found:
        raise SystemExit(
            "No reachable S40. Ensure same LAN, then: lennox-s40 discover\n"
            f"Config path: {config_path()}"
        )
    ip = found["ip"]
    host = found.get("host") or ""
    cfg = remember_success(cfg, ip=ip, app_id=app_id, host=host)
    print(f"lennox-s40: using {ip}" + (f" ({host})" if host else ""), file=sys.stderr)
    return ip, app_id, cfg


async def connect(ip: str, app_id: str) -> s30api_async:
    api = s30api_async(
        username="",
        password="",
        app_id=app_id,
        ip_address=ip,
        protocol="https",
        pii_message_logs=False,
        message_debug_logging=False,
        timeout=DEFAULT_TIMEOUT,
        long_poll_delay=6,
    )
    await api.serverConnect()
    return api


async def pump_until_ready(api: s30api_async):
    system = api.getSystem("LCC")
    await api.subscribe(system)
    for i in range(POLL_MAX):
        try:
            await api.messagePump()
        except Exception:
            if i % 15 == 0:
                try:
                    await api.serverConnect()
                    await api.subscribe(system)
                except Exception:
                    pass
            continue
        active = [z for z in system.zone_list if getattr(z, "temperature", None) is not None]
        if system.name and active:
            return system
    return system


@asynccontextmanager
async def session(args):
    """Resolve target (with rediscover), connect, yield (api, system, cfg); remember serial."""
    ip, app_id, cfg = resolve_target(args)
    api = await connect(ip, app_id)
    try:
        system = await pump_until_ready(api)
        serial = getattr(system, "serialNumber", None)
        if serial or system.wifi_ip:
            # Prefer wifi_ip from unit if present
            unit_ip = getattr(system, "wifi_ip", None) or ip
            cfg = remember_success(
                cfg,
                ip=unit_ip,
                app_id=app_id,
                host=cfg.get("host") or "",
                serial=serial,
            )
        yield api, system, cfg
    finally:
        try:
            await api.shutdown()
        except Exception:
            pass


def pick_zone(system, zone_arg: Optional[str]):
    zones = list(system.zone_list)
    active = [z for z in zones if getattr(z, "temperature", None) is not None]
    if not zones:
        raise SystemExit("No zones discovered yet")
    if zone_arg is None:
        if not active:
            raise SystemExit("No active zones with temperature data")
        return active[0]
    if zone_arg.isdigit():
        zid = int(zone_arg)
        for z in zones:
            if getattr(z, "id", None) == zid:
                return z
        raise SystemExit(f"No zone with id={zid}")
    needle = zone_arg.lower()
    for z in zones:
        if (getattr(z, "name", None) or "").lower() == needle:
            return z
    for z in zones:
        if needle in (getattr(z, "name", None) or "").lower():
            return z
    raise SystemExit(f"No zone matching {zone_arg!r}")


def zone_dict(z) -> dict:
    return {
        "id": getattr(z, "id", None),
        "name": getattr(z, "name", None),
        "temperature_F": getattr(z, "temperature", None),
        "humidity_pct": getattr(z, "humidity", None),
        "mode": getattr(z, "systemMode", None),
        "heat_setpoint_F": getattr(z, "hsp", None),
        "cool_setpoint_F": getattr(z, "csp", None),
        "single_setpoint_F": getattr(z, "sp", None),
        "fan": getattr(z, "fanMode", None),
        "humidity_mode": getattr(z, "humidityMode", None),
        "active": getattr(z, "temperature", None) is not None,
    }


def system_dict(system) -> dict:
    return {
        "name": system.name,
        "product": system.productType,
        "serial": system.serialNumber,
        "software": system.softwareVersion,
        "outdoor_temp_F": system.outdoorTemperature,
        "indoor_unit": system.indoorUnitType,
        "outdoor_unit": system.outdoorUnitType,
        "manual_away": system.manualAwayMode,
        "wifi_ip": system.wifi_ip,
        "wifi_ssid": system.wifi_ssid,
        "wifi_rssi": system.wifi_rssi,
        "alert": system.alert,
        "temp_unit": system.temperatureUnit,
        "single_setpoint_mode": system.single_setpoint_mode,
        "zones": [zone_dict(z) for z in system.zone_list],
    }


MODE_MAP = {
    "off": "off",
    "cool": "cool",
    "heat": "heat",
    "auto": "heat and cool",
    "heat_cool": "heat and cool",
    "heat-cool": "heat and cool",
    "heat and cool": "heat and cool",
}


async def cmd_status(args) -> int:
    async with session(args) as (_api, system, _cfg):
        print(json.dumps(system_dict(system), indent=2))
    return 0


async def cmd_mode(args) -> int:
    mode = MODE_MAP.get(args.mode.lower())
    if mode is None:
        raise SystemExit(f"Unknown mode {args.mode!r}; use off|cool|heat|auto")
    async with session(args) as (api, system, _cfg):
        z = pick_zone(system, args.zone)
        await z.setHVACMode(mode)
        for _ in range(5):
            await api.messagePump()
        print(json.dumps({"ok": True, "zone": zone_dict(z), "mode": mode}, indent=2))
    return 0


async def cmd_cool(args) -> int:
    async with session(args) as (api, system, _cfg):
        z = pick_zone(system, args.zone)
        await z.perform_setpoint(r_csp=float(args.temp_f))
        for _ in range(5):
            await api.messagePump()
        print(json.dumps({"ok": True, "zone": zone_dict(z)}, indent=2))
    return 0


async def cmd_heat(args) -> int:
    async with session(args) as (api, system, _cfg):
        z = pick_zone(system, args.zone)
        await z.perform_setpoint(r_hsp=float(args.temp_f))
        for _ in range(5):
            await api.messagePump()
        print(json.dumps({"ok": True, "zone": zone_dict(z)}, indent=2))
    return 0


async def cmd_fan(args) -> int:
    fan = args.fan.lower()
    if fan not in {"auto", "on", "circulate"}:
        raise SystemExit("fan must be auto|on|circulate")
    async with session(args) as (api, system, _cfg):
        z = pick_zone(system, args.zone)
        await z.setFanMode(fan)
        for _ in range(5):
            await api.messagePump()
        print(json.dumps({"ok": True, "zone": zone_dict(z), "fan": fan}, indent=2))
    return 0


async def cmd_away(args) -> int:
    on = args.state.lower() in {"on", "1", "true", "yes"}
    async with session(args) as (api, system, _cfg):
        await system.setManualAwayMode(on)
        for _ in range(5):
            await api.messagePump()
        print(json.dumps({"ok": True, "manual_away": system.manualAwayMode}, indent=2))
    return 0


def cmd_discover(args) -> int:
    """mDNS + Connect probe; persist first success to running config."""
    cfg = load_config()
    app_id = effective_app_id(args, cfg)
    print(f"config: {config_path()}")
    print("S40 mDNS: Lennox-S40-<SERIAL>.local  (_http._tcp / _icomfort4)")
    print(f"probe: POST https://<ip>/Endpoints/{app_id}/Connect  → 200/204")
    print()

    prefer = (getattr(args, "ip", None) or "").strip() or None
    found_any = False
    first: Optional[dict[str, str]] = None
    # Prefer named + mDNS; print all; fall back to LAN scan if none good
    candidates = discover_candidates(
        prefer=prefer, cfg=cfg, app_id=app_id, allow_lan_scan=False
    )
    for label, ip in candidates:
        ok = probe_connect(ip, app_id)
        mark = "GOOD" if ok else "fail"
        print(f"  {label} -> {ip}: Connect {mark}")
        if ok:
            found_any = True
            if first is None:
                host = label if (not _is_ip(label) and not label.startswith("lan-scan:")) else ""
                first = {"ip": ip, "host": host}
    if not first and not getattr(args, "no_lan_scan", False) and not ENV_NO_LAN_SCAN:
        print("  scanning local /24 for S40 Connect…")
        for label, ip in _scan_lan_lennox(app_id):
            print(f"  {label} -> {ip}: Connect GOOD")
            found_any = True
            first = {"ip": ip, "host": ""}
            break
    if first:
        cfg = remember_success(
            cfg,
            ip=first["ip"],
            app_id=app_id,
            host=first.get("host") or "",
        )
        print()
        print(f"saved config: ip={first['ip']} app_id={app_id}")
        print(f"  path: {config_path()}")
        print(f"export LENNOX_IP={first['ip']}")
    return 0 if found_any else 1


def cmd_config(args) -> int:
    path = config_path()
    sub = getattr(args, "config_cmd", "show")
    if sub == "path":
        print(path)
        return 0
    if sub == "clear":
        if os.path.isfile(path):
            os.remove(path)
            print(f"removed {path}")
        else:
            print(f"no config at {path}")
        return 0
    # show
    cfg = load_config()
    print(json.dumps({"path": path, "config": cfg}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lennox-s40", description="Lennox S40 local LAN control")
    p.add_argument(
        "--ip",
        default=None,
        help="thermostat IP or mDNS name (else LENNOX_IP, config, then discover)",
    )
    p.add_argument(
        "--app-id",
        default=DEFAULT_APP_ID,
        help="unique client application id",
    )
    p.add_argument(
        "--no-rediscover",
        action="store_true",
        help="do not auto-discover if last-known IP fails",
    )
    p.add_argument(
        "--no-lan-scan",
        action="store_true",
        help="skip local /24 Connect sweep during discovery",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="read system + zone state")
    s.set_defaults(func=lambda a: asyncio.run(cmd_status(a)))

    s = sub.add_parser("mode", help="set HVAC mode")
    s.add_argument("mode", help="off|cool|heat|auto")
    s.add_argument("--zone", help="zone id or name (default: first active)")
    s.set_defaults(func=lambda a: asyncio.run(cmd_mode(a)))

    s = sub.add_parser("cool", help="set cool setpoint °F")
    s.add_argument("temp_f", type=float)
    s.add_argument("--zone", help="zone id or name")
    s.set_defaults(func=lambda a: asyncio.run(cmd_cool(a)))

    s = sub.add_parser("heat", help="set heat setpoint °F")
    s.add_argument("temp_f", type=float)
    s.add_argument("--zone", help="zone id or name")
    s.set_defaults(func=lambda a: asyncio.run(cmd_heat(a)))

    s = sub.add_parser("fan", help="set fan mode")
    s.add_argument("fan", help="auto|on|circulate")
    s.add_argument("--zone", help="zone id or name")
    s.set_defaults(func=lambda a: asyncio.run(cmd_fan(a)))

    s = sub.add_parser("away", help="manual away mode")
    s.add_argument("state", help="on|off")
    s.set_defaults(func=lambda a: asyncio.run(cmd_away(a)))

    s = sub.add_parser("discover", help="probe LAN; save first good IP to config")
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("config", help="show/path/clear running config")
    cs = s.add_subparsers(dest="config_cmd")
    cs.add_parser("show", help="print config JSON (default)").set_defaults(config_cmd="show")
    cs.add_parser("path", help="print config file path").set_defaults(config_cmd="path")
    cs.add_parser("clear", help="delete config file").set_defaults(config_cmd="clear")
    s.set_defaults(func=cmd_config, config_cmd="show")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
