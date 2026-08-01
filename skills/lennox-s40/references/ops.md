# Operator recovery — lennox-s40

## Symptoms → actions

| Symptom | Action |
|---------|--------|
| Sticky wrong IP / always fails session | `lennox-s40 config clear` then `discover --verify-session` or `--ip <known>` |
| Phone app + skill fighting | Ensure unique `app_id` in config (auto-generated on 0.2+). Avoid shared library default. |
| Schedule stuck after setpoint | `lennox-s40 hold off --zone NAME` |
| Multi-zone write refused | Pass `--zone Downstairs` (or `--first-zone`) |
| LAN scan noise | Default off. Only use `--lan-scan` on trusted home LAN |
| IoT VLAN isolation | Put Mac/agent and S40 on same SSID/VLAN |
| Daily glitches | S40 network stack may reset; CLI reconnects within session deadline |

## Safe read path

```sh
lennox-s40 config show
lennox-s40 status          # redacted SSID/serial by default
lennox-s40 --full status   # diagnostics
```

## Pin a device

```sh
lennox-s40 --ip 192.168.x.y --no-rediscover status
# DHCP-reserve the thermostat MAC on the router
```

## Threat model (short)

Unauthenticated local HTTPS control by design. Anyone on the LAN who can reach port 443 can actuate HVAC if they speak the protocol. Do not expose the thermostat to the internet. Treat status JSON as household metadata (redacted by default).
