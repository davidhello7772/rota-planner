"""Encrypted connections whose certificate is really checked.

One place for the mail server (``mail.py``) and the WebDAV server
(``places.py``). On a Mac, the Python from python.org comes without a list of
trusted authorities (until its "Install Certificates" step is run, which needs
pip); the list of the system is then read with the Mac's own ``security``
tool. Nothing here ever turns checking off.
"""

from __future__ import annotations

import ssl
import subprocess
import sys
import threading
from typing import Optional

# Apple's own list of trusted authorities only: other keychains can hold certificates that are not trusted.
MAC_KEYCHAINS = ("/System/Library/Keychains/SystemRootCertificates.keychain",)
_mac_roots: Optional[str] = None
_lock = threading.Lock()


def _system_roots() -> str:
    """The Mac's trusted authorities, as PEM text (empty when they cannot be read)."""
    global _mac_roots
    with _lock:
        if _mac_roots is None:
            found = []
            for keychain in MAC_KEYCHAINS:
                try:
                    answer = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p", keychain],
                                            capture_output=True, text=True, timeout=10)
                    found.append(answer.stdout)
                except (OSError, subprocess.SubprocessError):
                    pass
            _mac_roots = "\n".join(found)
        return _mac_roots


def context() -> ssl.SSLContext:
    """A context that checks the server's certificate and name."""
    made = ssl.create_default_context()
    if sys.platform == "darwin" and not made.cert_store_stats().get("x509_ca"):
        roots = _system_roots()
        if "BEGIN CERTIFICATE" in roots:
            try:
                made.load_verify_locations(cadata=roots)
            except ssl.SSLError:
                pass  # checking stays on: the connection will be refused, and said so
    return made
