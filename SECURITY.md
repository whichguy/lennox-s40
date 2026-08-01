# Security

## Trust boundary

- Control is **local LAN only** over HTTPS with a **self-signed** device certificate.
- There is **no homeowner password** on the local API.
- Assume anyone on the same L2/L3 network who can reach TCP/443 may attempt control if they know the protocol.

## Mitigations in this skill

- Config file `0600`, config dir `0700`, flock around writes.
- Unique install-scoped `app_id` (not a shared library default).
- TLS peer CN preference for `Lennox` during probe.
- `/24` Connect sweep is **opt-in** (`--lan-scan`).
- Default status redacts SSID / serial / wifi IP (`--full` to reveal).
- Upstream message PII logging disabled.

## Reporting

Open a GitHub issue on [whichguy/lennox-s40](https://github.com/whichguy/lennox-s40) for security concerns. Do not file secrets or home IPs in public issues.
