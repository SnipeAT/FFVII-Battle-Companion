# Security policy

## Supported versions

Only the latest release gets fixes. Update to it before reporting: the [changelog](CHANGELOG.md) lists what each release fixed.

## What the companion exposes

The companion runs a small web server on your PC so its window and other devices can show the dashboard:

- By default it listens on `127.0.0.1` only: nothing else on your network can reach it.
- In LAN mode (`Launch_LAN_Mode.bat`, `--host 0.0.0.0`) every device on your network can open the dashboard and change the companion's settings. There is no login: use LAN mode only on networks you trust.
- It answers only requests addressed to an IP address, `localhost`, your PC's name or a name in `allowed_hosts`, and rejects state-changing requests and WebSockets from other web sites. How this works: [Architecture](docs/ARCHITECTURE.md#security-boundary).
- It sends nothing over the internet, and it reads only the screen region of the game window, never the game's files.

## Reporting a vulnerability

Please report security problems privately, not in a public issue: use **Report a vulnerability** on the repository's [Security tab](https://github.com/SnipeAT/FFVII-Battle-Companion/security) if it is offered, or otherwise open an issue that asks for a private contact without giving details. Include the version, how to reproduce the problem, and what an attacker could do with it.

Reports about the documented LAN-mode behavior above (no login on a trusted network) are not vulnerabilities, but ways to reach the companion that this page does not describe are.
