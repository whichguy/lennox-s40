#!/usr/bin/env python3
"""Local-only CLI for Lennox S40 (and S30/E30 LAN) thermostats.

Exit codes:
  0 ok
  1 unreachable / not found / soft closed
  2 missing deps
  3 bad request (args / validation)
  4 device / protocol error
  5 timeout / not ready

Running config: $LENNOX_CONFIG or ~/.config/lennox-s40/config.json
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

VERSION = "0.2.0"

# Lazy-loaded upstream client (optional for config/discover without venv)
s30api_async = None  # type: ignore
S30Exception = Exception  # type: ignore
_IMPORT_ERR: Optional[BaseException] = None


def _load_api() -> None:
    global s30api_async, S30Exception, _IMPORT_ERR
    if s30api_async is not None:
        return
    try:
        from lennoxs30api.s30api_async import s30api_async as _api
        from lennoxs30api.s30exception import S30Exception as _exc

        s30api_async = _api
        S30Exception = _exc
        _IMPORT_ERR = None
    except ImportError as e:
        _IMPORT_ERR = e
        print(
            "lennoxs30api not installed. Run: bash scripts/lennox-s40 --setup\n"
            "or: pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(2) from e


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"lennox-s40: warning: invalid {name}={raw!r}, using {default}", file=sys.stderr)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"lennox-s40: warning: invalid {name}={raw!r}, using {default}", file=sys.stderr)
        return default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


DEFAULT_TIMEOUT = _env_int("LENNOX_TIMEOUT", 90)
POLL_MAX = _env_int("LENNOX_POLL_MAX", 40)
PROBE_TIMEOUT = _env_float("LENNOX_PROBE_TIMEOUT", 4.0)
SESSION_DEADLINE = _env_float("LENNOX_SESSION_DEADLINE", 90.0)
ENV_NO_LAN_SCAN = _env_flag("LENNOX_NO_LAN_SCAN")
ENV_FULL = _env_flag("LENNOX_FULL")
CONFIG_VERSION = 1
LEGACY_SHARED_APP_ID = "mapp079372367644467046827200"

# Exit helpers
EX_OK = 0
EX_NOT_FOUND = 1
EX_DEPS = 2
EX_BAD_REQ = 3
EX_DEVICE = 4
EX_TIMEOUT = 5


class CliError(SystemExit):
    def __init__(self, code: int, message: str):
        super().__init__(code)
        self.code = code
        self.message = message
        print(f"lennox-s40: {message}", file=sys.stderr)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def config_path() -> str:
    if os.environ.get("LENNOX_CONFIG"):
        p = os.path.expanduser(os.environ["LENNOX_CONFIG"])
        return p if p else os.path.join(tempfile.gettempdir(), "lennox-s40-config.json")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg, "lennox-s40", "config.json")


def lock_path() -> str:
    return config_path() + ".lock"


@contextmanager
def config_lock():
    path = lock_path()
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


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
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    cfg = dict(cfg)
    cfg["version"] = CONFIG_VERSION
    cfg["updated_at"] = _utc_now()
    payload = json.dumps(cfg, indent=2, sort_keys=True) + "\n"
    with config_lock():
        fd, tmp = tempfile.mkstemp(prefix=".lennox-", suffix=".tmp", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            try:
                dir_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def generate_app_id() -> str:
    return f"mapp{secrets.randbelow(10**24):024d}"


def env_ip() -> str:
    return os.environ.get("LENNOX_IP", "").strip()


def env_app_id() -> str:
    return os.environ.get("LENNOX_APP_ID", "").strip()


def effective_app_id(args, cfg: dict[str, Any]) -> str:
    """Precedence: --app-id > LENNOX_APP_ID > config (non-legacy) > generate."""
    flag = getattr(args, "app_id", None)
    if flag:
        return str(flag).strip()
    if env_app_id():
        return env_app_id()
    existing = (cfg.get("app_id") or "").strip()
    if existing and existing != LEGACY_SHARED_APP_ID:
        return existing
    # migrate off shared library default
    new_id = generate_app_id()
    cfg = dict(cfg)
    cfg["app_id"] = new_id
    save_config(cfg)
    print(f"lennox-s40: generated unique app_id (saved to config)", file=sys.stderr)
    return new_id


def resolve_host(host: str) -> str:
    host = host.strip()
    if not host:
        raise CliError(EX_BAD_REQ, "empty host")
    if _is_ip(host):
        return host
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as e:
        raise CliError(EX_NOT_FOUND, f"Cannot resolve host {host!r}: {e}") from e


def _is_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _tls_peer_cn(ip: str, timeout: float = PROBE_TIMEOUT) -> Optional[str]:
    try:
        ctx = ssl._create_unverified_context()
        with socket.create_connection((ip, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=ip) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                if not cert:
                    # binary form for self-signed often empty subject in getpeercert
                    der = ssock.getpeercert(binary_form=True)
                    if not der:
                        return None
                    # parse CN roughly from openssl text if needed
                    return _cn_from_der(der)
                subj = cert.get("subject", ())
                for rdn in subj:
                    for k, v in rdn:
                        if k == "commonName":
                            return v
    except Exception:
        return None
    return None


def _cn_from_der(der: bytes) -> Optional[str]:
    try:
        # stdlib only: use ssl to re-dump via openssl not available; try cryptography-free regex on openssl x509
        # Fallback: shell openssl if present
        import subprocess as sp

        p = sp.run(
            ["openssl", "x509", "-noout", "-subject", "-inform", "DER"],
            input=der,
            capture_output=True,
            timeout=2,
        )
        text = (p.stdout or b"").decode("utf-8", "replace")
        m = re.search(r"CN\s*=\s*([^,/]+)", text)
        return m.group(1).strip() if m else None
    except Exception:
        return None


def probe_connect(
    ip: str,
    app_id: str,
    timeout: float = PROBE_TIMEOUT,
    *,
    require_lennox_cn: bool = True,
) -> bool:
    """HTTPS Connect + optional CN=Lennox identity check."""
    if require_lennox_cn:
        cn = _tls_peer_cn(ip, timeout=timeout)
        if cn is not None and "lennox" not in cn.lower():
            return False
        # if cn is None (parse failed), still try HTTP — many S40 certs parse poorly
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
    for m in re.finditer(r"Lennox-(?:S40|S30|E30)-[A-Za-z0-9]+\.local", out, re.I):
        h = m.group(0)
        if h not in hosts:
            hosts.append(h)
    return hosts


def _local_ipv4_prefixes() -> list[str]:
    prefixes: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if _is_ip(ip) and not ip.startswith("127."):
            prefixes.append(".".join(ip.split(".")[:3]) + ".")
    except OSError:
        pass
    return prefixes


def _scan_lan_lennox(app_id: str, prefixes: Optional[list[str]] = None) -> list[tuple[str, str]]:
    import concurrent.futures

    prefixes = prefixes or _local_ipv4_prefixes()
    if not prefixes:
        return []
    targets = [f"{pref}{i}" for pref in prefixes for i in range(1, 255)]
    found: list[tuple[str, str]] = []

    def check(ip: str) -> Optional[str]:
        return ip if probe_connect(ip, app_id, timeout=1.5) else None

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
    exclude: Optional[set[str]] = None,
) -> list[tuple[str, str]]:
    cfg = cfg or {}
    exclude = exclude or set()
    labels: list[str] = []
    if prefer:
        labels.append(prefer)
    eip = env_ip()
    if eip and eip not in labels:
        labels.append(eip)
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
    seen: set[str] = set()
    for lab in labels:
        try:
            ip = resolve_host(lab)
        except SystemExit:
            continue
        if ip in exclude or ip in seen:
            continue
        seen.add(ip)
        out.append((lab, ip))
    return out


def discover_live(
    app_id: str,
    *,
    prefer: Optional[str] = None,
    cfg: Optional[dict[str, Any]] = None,
    quiet: bool = False,
    allow_lan_scan: bool = False,
    exclude: Optional[set[str]] = None,
) -> Optional[dict[str, str]]:
    if ENV_NO_LAN_SCAN:
        allow_lan_scan = False
    candidates = discover_candidates(prefer=prefer, cfg=cfg, exclude=exclude)
    for label, ip in candidates:
        ok = probe_connect(ip, app_id)
        if not quiet:
            print(f"  probe {label} -> {ip}: {'OK' if ok else 'fail'}", file=sys.stderr)
        if ok:
            host = ""
            if not _is_ip(label) and not label.startswith("lan-scan:"):
                host = label
            return {"ip": ip, "host": host}
    if allow_lan_scan:
        if not quiet:
            print("  named candidates failed; scanning local /24 (opt-in)…", file=sys.stderr)
        for label, ip in _scan_lan_lennox(app_id):
            if exclude and ip in exclude:
                continue
            if not quiet:
                print(f"  probe {label} -> {ip}: OK", file=sys.stderr)
            return {"ip": ip, "host": ""}
    return None


def remember_identity(
    cfg: dict[str, Any],
    *,
    ip: str,
    app_id: str,
    host: str = "",
    serial: Optional[str] = None,
    clear_host: bool = False,
    product: Optional[str] = None,
) -> dict[str, Any]:
    cfg = dict(cfg)
    cfg["ip"] = ip
    cfg["app_id"] = app_id
    if clear_host:
        cfg.pop("host", None)
    elif host:
        cfg["host"] = host
    if serial:
        cfg["serial"] = serial
    if product:
        cfg["product"] = product
    cfg["last_ok_at"] = _utc_now()
    save_config(cfg)
    return cfg


def resolve_target(args) -> tuple[str, str, dict[str, Any]]:
    """Resolve (ip, app_id, cfg). Does NOT persist until session proves identity."""
    cfg = load_config()
    app_id = effective_app_id(args, cfg)
    # reload if generate mutated config
    cfg = load_config()
    explicit_ip = (getattr(args, "ip", None) or "").strip() or None
    rediscover = not getattr(args, "no_rediscover", False)
    # LAN scan opt-in only (--lan-scan)
    allow_lan = bool(getattr(args, "lan_scan", False)) and not ENV_NO_LAN_SCAN

    primary: Optional[str] = None
    if explicit_ip:
        primary = explicit_ip
    elif env_ip():
        primary = env_ip()
    elif (cfg.get("ip") or "").strip():
        primary = str(cfg["ip"]).strip()
    elif (cfg.get("host") or "").strip():
        primary = str(cfg["host"]).strip()

    if primary:
        try:
            ip = resolve_host(primary)
        except SystemExit as e:
            if not rediscover:
                raise
            print(f"lennox-s40: resolve failed ({e}); rediscovering…", file=sys.stderr)
            ip = ""
        else:
            if probe_connect(ip, app_id):
                return ip, app_id, cfg
            if not rediscover:
                raise CliError(
                    EX_NOT_FOUND,
                    f"Connect failed for {ip}. Fix --ip / LENNOX_IP or omit --no-rediscover.",
                )
            print(f"lennox-s40: Connect failed for {ip}; rediscovering…", file=sys.stderr)
            found = discover_live(
                app_id,
                prefer=None,
                cfg=cfg,
                allow_lan_scan=allow_lan,
                exclude={ip},
            )
            if not found:
                raise CliError(EX_NOT_FOUND, f"No reachable S40 after failing {ip}")
            print(f"lennox-s40: using {found['ip']}", file=sys.stderr)
            return found["ip"], app_id, cfg
    elif not rediscover:
        raise CliError(
            EX_NOT_FOUND,
            f"No thermostat address. Set --ip, LENNOX_IP, or run discover. Config: {config_path()}",
        )

    found = discover_live(
        app_id,
        prefer=primary,
        cfg=cfg,
        allow_lan_scan=allow_lan,
    )
    if not found:
        raise CliError(EX_NOT_FOUND, f"No reachable S40. Config: {config_path()}")
    print(f"lennox-s40: using {found['ip']}", file=sys.stderr)
    return found["ip"], app_id, cfg


async def connect(ip: str, app_id: str):
    _load_api()
    assert s30api_async is not None
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


async def pump_until_ready(api, deadline: float = SESSION_DEADLINE):
    system = api.getSystem("LCC")
    await api.subscribe(system)
    start = time.monotonic()
    i = 0
    while time.monotonic() - start < deadline and i < POLL_MAX:
        i += 1
        try:
            await api.messagePump()
        except Exception:
            await asyncio.sleep(min(2.0, 0.2 * i))
            if i % 5 == 0:
                try:
                    await api.serverConnect()
                    await api.subscribe(system)
                except Exception:
                    pass
            continue
        active = [z for z in system.zone_list if getattr(z, "temperature", None) is not None]
        if system.name and active:
            return system, True
    return system, False


@asynccontextmanager
async def session(args):
    """Full session with reconnect+rediscover once on connect failure."""
    rediscover = not getattr(args, "no_rediscover", False)
    allow_lan = bool(getattr(args, "lan_scan", False)) and not ENV_NO_LAN_SCAN
    explicit_ip = (getattr(args, "ip", None) or "").strip() or None

    ip, app_id, cfg = resolve_target(args)
    api = None
    try:
        try:
            api = await connect(ip, app_id)
            system, ready = await pump_until_ready(api)
        except Exception as e:
            if not rediscover:
                raise CliError(EX_DEVICE, f"session failed: {e}") from e
            print(f"lennox-s40: session failed ({e}); rediscovering…", file=sys.stderr)
            if api is not None:
                try:
                    await api.shutdown()
                except Exception:
                    pass
            found = discover_live(
                app_id,
                cfg=cfg,
                allow_lan_scan=allow_lan,
                exclude={ip},
            )
            if not found:
                raise CliError(EX_NOT_FOUND, f"No reachable S40 after session fail on {ip}") from e
            ip = found["ip"]
            api = await connect(ip, app_id)
            system, ready = await pump_until_ready(api)

        if not ready and getattr(args, "cmd", None) != "status":
            # writes require ready; status may return degraded with flag
            if getattr(args, "require_ready", True) and getattr(args, "cmd", "") not in {
                "status",
                None,
            }:
                raise CliError(EX_TIMEOUT, "system not ready (no active zones within deadline)")

        serial = getattr(system, "serialNumber", None)
        product = getattr(system, "productType", None)
        # Identity: if explicit IP, clear stale host unless it matches this unit's serial
        clear_host = bool(explicit_ip)
        host = ""
        if not clear_host:
            host = (cfg.get("host") or "") if not explicit_ip else ""
        if serial:
            host = f"Lennox-S40-{serial}.local"
            clear_host = False
        cfg = remember_identity(
            cfg,
            ip=ip,
            app_id=app_id,
            host=host,
            serial=serial,
            clear_host=clear_host and not serial,
            product=product,
        )
        yield api, system, cfg, ready
    finally:
        if api is not None:
            try:
                await api.shutdown()
            except Exception:
                pass


def pick_zone(system, zone_arg: Optional[str], *, first_zone: bool = False, for_write: bool = False):
    zones = list(system.zone_list)
    active = [z for z in zones if getattr(z, "temperature", None) is not None]
    if not zones:
        raise CliError(EX_DEVICE, "No zones discovered yet")
    if zone_arg is None:
        if for_write and len(active) > 1 and not first_zone:
            names = ", ".join(getattr(z, "name", "?") or "?" for z in active)
            raise CliError(
                EX_BAD_REQ,
                f"Multiple active zones ({names}); pass --zone NAME or --first-zone",
            )
        if not active:
            raise CliError(EX_DEVICE, "No active zones with temperature data")
        return active[0]
    if zone_arg.isdigit():
        zid = int(zone_arg)
        for z in zones:
            if getattr(z, "id", None) == zid:
                if for_write and getattr(z, "temperature", None) is None:
                    raise CliError(EX_BAD_REQ, f"Zone id={zid} is inactive")
                return z
        raise CliError(EX_BAD_REQ, f"No zone with id={zid}")
    needle = zone_arg.lower()
    exact = [z for z in zones if (getattr(z, "name", None) or "").lower() == needle]
    if len(exact) == 1:
        z = exact[0]
        if for_write and getattr(z, "temperature", None) is None:
            raise CliError(EX_BAD_REQ, f"Zone {zone_arg!r} is inactive")
        return z
    if len(exact) > 1:
        raise CliError(EX_BAD_REQ, f"Ambiguous zone name {zone_arg!r}")
    partial = [z for z in zones if needle in (getattr(z, "name", None) or "").lower()]
    if for_write:
        raise CliError(EX_BAD_REQ, f"No exact zone match for {zone_arg!r} (substring match disabled for writes)")
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise CliError(EX_BAD_REQ, f"Ambiguous zone substring {zone_arg!r}")
    raise CliError(EX_BAD_REQ, f"No zone matching {zone_arg!r}")


def _zone_hold(z) -> Optional[bool]:
    v = getattr(z, "scheduleHold", None)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in {"true", "1", "yes"}
    return bool(v)


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
        "schedule_hold": _zone_hold(z),
        "override_active": getattr(z, "overrideActive", None)
        if hasattr(z, "overrideActive")
        else getattr(z, "isZoneOveride", lambda: None)()
        if callable(getattr(z, "isZoneOveride", None))
        else None,
        "active": getattr(z, "temperature", None) is not None,
    }


def system_dict(system, *, full: bool = False) -> dict:
    serial = getattr(system, "serialNumber", None)
    ssid = getattr(system, "wifi_ssid", None)
    wip = getattr(system, "wifi_ip", None)
    if not full:
        if serial and len(str(serial)) > 4:
            serial = "…" + str(serial)[-4:]
        if ssid:
            ssid = "<redacted>"
        if wip:
            wip = "<redacted>"
    return {
        "name": getattr(system, "name", None),
        "product": getattr(system, "productType", None),
        "serial": serial,
        "software": getattr(system, "softwareVersion", None),
        "outdoor_temp_F": getattr(system, "outdoorTemperature", None),
        "indoor_unit": getattr(system, "indoorUnitType", None),
        "outdoor_unit": getattr(system, "outdoorUnitType", None),
        "manual_away": getattr(system, "manualAwayMode", None),
        "wifi_ip": wip,
        "wifi_ssid": ssid,
        "wifi_rssi": getattr(system, "wifi_rssi", None) if full else None,
        "alert": getattr(system, "alert", None),
        "temp_unit": getattr(system, "temperatureUnit", None),
        "single_setpoint_mode": getattr(system, "single_setpoint_mode", None),
        "zones": [zone_dict(z) for z in getattr(system, "zone_list", []) or []],
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


def _finite_temp(v: float, label: str) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as e:
        raise CliError(EX_BAD_REQ, f"invalid {label}") from e
    if not math.isfinite(x):
        raise CliError(EX_BAD_REQ, f"{label} must be finite")
    if x < 40 or x > 99:
        raise CliError(EX_BAD_REQ, f"{label} out of range (40–99 °F)")
    return x


async def _pump_n(api, n: int = 8) -> None:
    for _ in range(n):
        try:
            await api.messagePump()
        except Exception:
            pass


async def _verify_zone(api, z, predicate, timeout: float = 15.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            await api.messagePump()
        except Exception:
            await asyncio.sleep(0.3)
            continue
        if predicate(z):
            return True
    return False


async def cmd_status(args) -> int:
    async with session(args) as (_api, system, _cfg, ready):
        out = system_dict(system, full=bool(getattr(args, "full", False) or ENV_FULL))
        out["ready"] = ready
        print(json.dumps(out, indent=2))
        if not ready:
            return EX_TIMEOUT
    return EX_OK


async def cmd_mode(args) -> int:
    mode = MODE_MAP.get(args.mode.lower())
    if mode is None:
        raise CliError(EX_BAD_REQ, f"Unknown mode {args.mode!r}; use off|cool|heat|auto")
    async with session(args) as (api, system, _cfg, ready):
        if not ready:
            raise CliError(EX_TIMEOUT, "system not ready")
        z = pick_zone(system, args.zone, first_zone=args.first_zone, for_write=True)
        print(f"lennox-s40: target zone {getattr(z, 'name', z.id)!r}", file=sys.stderr)
        await z.setHVACMode(mode)
        ok = await _verify_zone(api, z, lambda zz: getattr(zz, "systemMode", None) == mode)
        print(
            json.dumps(
                {"ok": ok, "verified": ok, "mode": mode, "zone": zone_dict(z)},
                indent=2,
            )
        )
        return EX_OK if ok else EX_TIMEOUT


async def cmd_cool(args) -> int:
    temp = _finite_temp(args.temp_f, "cool setpoint")
    async with session(args) as (api, system, _cfg, ready):
        if not ready:
            raise CliError(EX_TIMEOUT, "system not ready")
        z = pick_zone(system, args.zone, first_zone=args.first_zone, for_write=True)
        print(f"lennox-s40: target zone {getattr(z, 'name', z.id)!r}", file=sys.stderr)
        hold_before = _zone_hold(z)
        ssp = bool(getattr(system, "single_setpoint_mode", False))
        if ssp:
            await z.perform_setpoint(r_sp=temp)
            pred = lambda zz: getattr(zz, "sp", None) == temp
        else:
            await z.perform_setpoint(r_csp=temp)
            pred = lambda zz: getattr(zz, "csp", None) == temp
        ok = await _verify_zone(api, z, pred)
        hold_after = _zone_hold(z)
        print(
            json.dumps(
                {
                    "ok": ok,
                    "verified": ok,
                    "zone": zone_dict(z),
                    "schedule_hold_created": bool(hold_after and not hold_before),
                    "single_setpoint_mode": ssp,
                },
                indent=2,
            )
        )
        return EX_OK if ok else EX_TIMEOUT


async def cmd_heat(args) -> int:
    temp = _finite_temp(args.temp_f, "heat setpoint")
    async with session(args) as (api, system, _cfg, ready):
        if not ready:
            raise CliError(EX_TIMEOUT, "system not ready")
        z = pick_zone(system, args.zone, first_zone=args.first_zone, for_write=True)
        print(f"lennox-s40: target zone {getattr(z, 'name', z.id)!r}", file=sys.stderr)
        hold_before = _zone_hold(z)
        ssp = bool(getattr(system, "single_setpoint_mode", False))
        if ssp:
            await z.perform_setpoint(r_sp=temp)
            pred = lambda zz: getattr(zz, "sp", None) == temp
        else:
            await z.perform_setpoint(r_hsp=temp)
            pred = lambda zz: getattr(zz, "hsp", None) == temp
        ok = await _verify_zone(api, z, pred)
        hold_after = _zone_hold(z)
        print(
            json.dumps(
                {
                    "ok": ok,
                    "verified": ok,
                    "zone": zone_dict(z),
                    "schedule_hold_created": bool(hold_after and not hold_before),
                    "single_setpoint_mode": ssp,
                },
                indent=2,
            )
        )
        return EX_OK if ok else EX_TIMEOUT


async def cmd_fan(args) -> int:
    fan = args.fan.lower()
    if fan not in {"auto", "on", "circulate"}:
        raise CliError(EX_BAD_REQ, "fan must be auto|on|circulate")
    async with session(args) as (api, system, _cfg, ready):
        if not ready:
            raise CliError(EX_TIMEOUT, "system not ready")
        z = pick_zone(system, args.zone, first_zone=args.first_zone, for_write=True)
        print(f"lennox-s40: target zone {getattr(z, 'name', z.id)!r}", file=sys.stderr)
        await z.setFanMode(fan)
        ok = await _verify_zone(api, z, lambda zz: getattr(zz, "fanMode", None) == fan)
        print(json.dumps({"ok": ok, "verified": ok, "fan": fan, "zone": zone_dict(z)}, indent=2))
        return EX_OK if ok else EX_TIMEOUT


async def cmd_away(args) -> int:
    state = args.state.lower()
    if state not in {"on", "off"}:
        raise CliError(EX_BAD_REQ, "away must be on|off")
    on = state == "on"
    async with session(args) as (api, system, _cfg, ready):
        if not ready:
            raise CliError(EX_TIMEOUT, "system not ready")
        await system.setManualAwayMode(on)
        ok = await _verify_zone(
            api, system, lambda s: bool(getattr(s, "manualAwayMode", None)) is on, timeout=15.0
        )
        # _verify_zone expects zone-like; use manual pump
        await _pump_n(api, 8)
        ok = bool(getattr(system, "manualAwayMode", None)) is on
        print(json.dumps({"ok": ok, "verified": ok, "manual_away": system.manualAwayMode}, indent=2))
        return EX_OK if ok else EX_TIMEOUT


async def cmd_hold(args) -> int:
    state = args.state.lower()
    if state not in {"on", "off", "status"}:
        raise CliError(EX_BAD_REQ, "hold must be on|off|status")
    async with session(args) as (api, system, _cfg, ready):
        if not ready:
            raise CliError(EX_TIMEOUT, "system not ready")
        z = pick_zone(
            system,
            args.zone,
            first_zone=args.first_zone or state == "status",
            for_write=state != "status",
        )
        if state == "status":
            print(json.dumps({"zone": zone_dict(z), "schedule_hold": _zone_hold(z)}, indent=2))
            return EX_OK
        hold = state == "on"
        if not hasattr(z, "setScheduleHold"):
            raise CliError(EX_DEVICE, "setScheduleHold not available on this firmware/API")
        await z.setScheduleHold(hold)
        await _pump_n(api, 8)
        print(
            json.dumps(
                {"ok": True, "schedule_hold": _zone_hold(z), "zone": zone_dict(z)},
                indent=2,
            )
        )
        return EX_OK


def cmd_discover(args) -> int:
    cfg = load_config()
    app_id = effective_app_id(args, cfg)
    cfg = load_config()
    print(f"config: {config_path()}")
    print(f"probe: POST https://<ip>/Endpoints/{app_id}/Connect (+ CN=Lennox preferred)")
    print()
    prefer = (getattr(args, "ip", None) or "").strip() or None
    allow_lan = bool(getattr(args, "lan_scan", False)) and not ENV_NO_LAN_SCAN
    found_any = False
    first: Optional[dict[str, str]] = None
    for label, ip in discover_candidates(prefer=prefer, cfg=cfg):
        ok = probe_connect(ip, app_id)
        print(f"  {label} -> {ip}: {'GOOD' if ok else 'fail'}")
        if ok and first is None:
            found_any = True
            host = label if (not _is_ip(label) and not label.startswith("lan-scan:")) else ""
            first = {"ip": ip, "host": host}
    if not first and allow_lan:
        print("  scanning local /24 (opt-in)…")
        for label, ip in _scan_lan_lennox(app_id):
            print(f"  {label} -> {ip}: GOOD")
            found_any = True
            first = {"ip": ip, "host": ""}
            break
    if first:
        # Persist only after optional full session if --verify-session
        if getattr(args, "verify_session", False):
            async def _verify():
                api = await connect(first["ip"], app_id)
                try:
                    system, ready = await pump_until_ready(api)
                    if not ready:
                        raise CliError(EX_TIMEOUT, "discovered but system not ready")
                    return remember_identity(
                        cfg,
                        ip=first["ip"],
                        app_id=app_id,
                        host=first.get("host") or "",
                        serial=getattr(system, "serialNumber", None),
                        product=getattr(system, "productType", None),
                    )
                finally:
                    await api.shutdown()

            asyncio.run(_verify())
        else:
            # still require CN probe already done; persist probe-level for ops convenience
            # but mark as unverified_session
            cfg2 = remember_identity(
                cfg,
                ip=first["ip"],
                app_id=app_id,
                host=first.get("host") or "",
            )
            cfg2["session_verified"] = False
            save_config(cfg2)
        print()
        print(f"saved config: ip={first['ip']} app_id={app_id}")
        print(f"  path: {config_path()}")
    return EX_OK if found_any else EX_NOT_FOUND


def cmd_config(args) -> int:
    path = config_path()
    sub = getattr(args, "config_cmd", "show") or "show"
    if sub == "path":
        print(path)
        return EX_OK
    if sub == "clear":
        with config_lock():
            if os.path.isfile(path):
                os.remove(path)
                print(f"removed {path}")
            else:
                print(f"no config at {path}")
        return EX_OK
    cfg = load_config()
    print(json.dumps({"path": path, "config": cfg}, indent=2))
    return EX_OK


def cmd_version(_args) -> int:
    print(VERSION)
    return EX_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lennox-s40", description="Lennox S40 local LAN control")
    p.add_argument("--ip", default=None, help="thermostat IP or mDNS name")
    p.add_argument("--app-id", default=None, help="unique client application id")
    p.add_argument("--no-rediscover", action="store_true")
    p.add_argument(
        "--lan-scan",
        action="store_true",
        help="opt-in /24 Connect sweep (default off)",
    )
    p.add_argument("--no-lan-scan", action="store_true", help="legacy alias: force off")
    p.add_argument("--full", action="store_true", help="do not redact SSID/serial/IP in status")
    p.add_argument("--first-zone", action="store_true", help="allow default first zone on writes")
    p.add_argument("--version", action="store_true", help="print version and exit")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status")
    s.set_defaults(func=lambda a: asyncio.run(cmd_status(a)))

    s = sub.add_parser("mode")
    s.add_argument("mode", choices=["off", "cool", "heat", "auto", "heat_cool", "heat-cool"])
    s.add_argument("--zone")
    s.set_defaults(func=lambda a: asyncio.run(cmd_mode(a)))

    s = sub.add_parser("cool")
    s.add_argument("temp_f", type=float)
    s.add_argument("--zone")
    s.set_defaults(func=lambda a: asyncio.run(cmd_cool(a)))

    s = sub.add_parser("heat")
    s.add_argument("temp_f", type=float)
    s.add_argument("--zone")
    s.set_defaults(func=lambda a: asyncio.run(cmd_heat(a)))

    s = sub.add_parser("fan")
    s.add_argument("fan", choices=["auto", "on", "circulate"])
    s.add_argument("--zone")
    s.set_defaults(func=lambda a: asyncio.run(cmd_fan(a)))

    s = sub.add_parser("away")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(func=lambda a: asyncio.run(cmd_away(a)))

    s = sub.add_parser("hold")
    s.add_argument("state", choices=["on", "off", "status"])
    s.add_argument("--zone")
    s.set_defaults(func=lambda a: asyncio.run(cmd_hold(a)))

    s = sub.add_parser("discover")
    s.add_argument(
        "--verify-session",
        action="store_true",
        help="full subscribe before saving config",
    )
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("config")
    cs = s.add_subparsers(dest="config_cmd")
    for name in ("show", "path", "clear"):
        cs.add_parser(name).set_defaults(config_cmd=name)
    s.set_defaults(func=cmd_config, config_cmd="show")

    s = sub.add_parser("version")
    s.set_defaults(func=cmd_version)

    return p


def main(argv=None) -> int:
    # honor legacy --no-lan-scan by mapping to not lan_scan
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 1

    if getattr(args, "version", False) and not args.cmd:
        return cmd_version(args)

    if not args.cmd:
        parser.print_help()
        return EX_BAD_REQ

    # legacy flag: --no-lan-scan means do not enable scan
    if getattr(args, "no_lan_scan", False):
        args.lan_scan = False

    try:
        return int(args.func(args) or 0)
    except CliError as e:
        return int(e.code)
    except SystemExit as e:
        if isinstance(e.code, int):
            return e.code
        return EX_NOT_FOUND
    except Exception as e:
        name = type(e).__name__
        if name == "S30Exception" or "S30" in name:
            print(f"lennox-s40: device error: {e}", file=sys.stderr)
            return EX_DEVICE
        print(f"lennox-s40: unexpected error: {e}", file=sys.stderr)
        return EX_DEVICE


if __name__ == "__main__":
    sys.exit(main())
