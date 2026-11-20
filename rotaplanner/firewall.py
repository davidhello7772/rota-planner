"""Looking at this computer's firewall, to advise when other computers are allowed.

The program never changes the firewall: that needs administrator rights and
must stay a deliberate act. It only *looks* (read-only system tools, fixed
arguments, no shell, short timeout, never elevated) and shows the narrowest
command to copy: this port, TCP, from the allowed addresses only.

Addresses come from ``access.parse_allowed`` (``ipaddress`` objects), the port
is an integer: nothing typed by a user reaches a command line as such.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .access import Network

Json = Dict[str, object]
Runner = Callable[[Sequence[str]], Optional[str]]
TIMEOUT_SECONDS = 5
RULE_NAME = "Rota Planner"
MAC_FIREWALL = "/usr/libexec/ApplicationFirewall/socketfilterfw"
UFW_CONF = Path("/etc/ufw/ufw.conf")


def run(args: Sequence[str]) -> Optional[str]:
    """Output of a read-only tool, or None if it is missing, refused or too slow."""
    program = shutil.which(args[0]) or (args[0] if os.path.isabs(args[0]) and os.path.exists(args[0]) else None)
    if program is None:
        return None
    try:
        done = subprocess.run([program, *args[1:]], capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
                              check=False, stdin=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def _ufw_enabled(read: Callable[[Path], Optional[str]]) -> Optional[bool]:
    text = read(UFW_CONF)
    if text is None:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ENABLED":
            return value.strip().strip("'\"").lower() == "yes"
    return None


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _sources(allowed: List[Network]) -> List[str]:
    return [str(network.network_address) if network.num_addresses == 1 else str(network) for network in allowed]


def commands(system: str, tool: str, port: int, allowed: List[Network]) -> Json:
    """What to type to open the port to the allowed addresses only, and to close it again."""
    port = int(port)
    sources = _sources(allowed)
    if system == "Windows":
        remote = ",".join(sources) or "LocalSubnet"
        return {
            "where": "In PowerShell opened with “Run as administrator”:",
            "open": [f'New-NetFirewallRule -DisplayName "{RULE_NAME}" -Direction Inbound -Protocol TCP '
                     f'-LocalPort {port} -RemoteAddress {remote} -Profile Private -Action Allow'],
            "close": [f'Remove-NetFirewallRule -DisplayName "{RULE_NAME}"'],
            "note": "If Windows asks whether to let Python use the network, allow it for private networks only, never public ones. "
                    "If you answered Cancel before, Windows blocks Python: remove that block in Windows Security › Firewall › Allow an app.",
        }
    if system == "Darwin":
        return {
            "where": "",
            "open": [], "close": [],
            "note": "The macOS firewall works per program, not per port. When macOS asks whether Python may accept incoming "
                    "connections, choose Allow. Rota Planner itself answers only the allowed addresses, after the password.",
        }
    if tool == "firewalld":
        rules = [f"rule family=\"{'ipv6' if ':' in s else 'ipv4'}\" source address=\"{s}\" port port=\"{port}\" protocol=\"tcp\" accept"
                 for s in sources]
        return {
            "where": "In a terminal:",
            "open": [f"sudo firewall-cmd --permanent --add-rich-rule='{r}'" for r in rules] + ["sudo firewall-cmd --reload"],
            "close": [f"sudo firewall-cmd --permanent --remove-rich-rule='{r}'" for r in rules] + ["sudo firewall-cmd --reload"],
            "note": "",
        }
    if tool == "nftables":
        return {
            "where": "", "open": [], "close": [],
            "note": f"This computer uses nftables rules written by hand: add one accepting TCP port {port} "
                    f"from {', '.join(sources)} only.",
        }
    return {
        "where": "In a terminal:",
        "open": [f"sudo ufw allow from {s} to any port {port} proto tcp" for s in sources],
        "close": [f"sudo ufw delete allow from {s} to any port {port} proto tcp" for s in sources],
        "note": "" if tool == "ufw" else "These commands are for ufw, the usual firewall of Ubuntu (sudo apt install ufw, then sudo ufw enable).",
    }


def status(port: int, allowed: List[Network], system: Optional[str] = None, runner: Runner = run,
           read: Callable[[Path], Optional[str]] = _read) -> Json:
    """Firewall on, off or unknown; whether a rule for the port could be seen; the commands.

    ``state``: "on", "off" or "unknown" (could not be checked without
    administrator rights: said so, never guessed). ``rule``: True, False or
    None (could not be read).
    """
    system = system or platform.system()
    state, tool, rule, detail = "unknown", "", None, ""
    if system == "Windows":
        tool = "windows"
        out = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      "Get-NetFirewallProfile | ForEach-Object { $_.Name + '=' + $_.Enabled }"])
        if out is not None:
            profiles = dict(line.strip().partition("=")[::2] for line in out.splitlines() if "=" in line)
            on = [name for name, value in profiles.items() if value.strip().lower() == "true"]
            state = "on" if on else ("off" if profiles else "unknown")
            detail = f"On for: {', '.join(on)}." if on else ""
            found = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-NetFirewallRule -DisplayName '{RULE_NAME}' -ErrorAction SilentlyContinue | Measure-Object).Count"])
            if found is not None and found.strip().isdigit():
                rule = int(found.strip()) > 0
    elif system == "Darwin":
        tool = "macos"
        out = runner([MAC_FIREWALL, "--getglobalstate"])
        if out is not None:
            text = out.lower()
            state = "on" if "enabled" in text or "state = 1" in text or "state = 2" in text else (
                "off" if "disabled" in text or "state = 0" in text else "unknown")
    else:
        for name in ("ufw", "firewalld", "nftables"):
            active = runner(["systemctl", "is-active", name])
            if active is not None and active.strip() == "active":
                tool = name
                break
        if tool == "ufw":
            enabled = _ufw_enabled(read)
            state = "unknown" if enabled is None else ("on" if enabled else "off")
            detail = "" if enabled is not None else "ufw is installed; whether it is on needs administrator rights to see."
        elif tool == "firewalld":
            state = "on"
        elif tool == "nftables":
            state = "on"
        else:
            enabled = _ufw_enabled(read)
            if enabled is not None:
                tool, state = "ufw", ("on" if enabled else "off")
            else:
                state = "off"
                detail = "No firewall found (ufw, firewalld or nftables)."
    return {"system": system, "tool": tool, "state": state, "rule": rule, "detail": detail,
            "commands": commands(system, tool, port, allowed)}
