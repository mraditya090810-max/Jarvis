"""
wifi_pair.py
-----------------------------------
LAN pairing manager for JARVIS

Purpose:
    Replace QR pairing with a simple pairing code.

Example:

PC
--------
Pair Code:
538271

Phone
--------
Enter:
192.168.1.25
538271

Done.
"""

from __future__ import annotations

import secrets
import socket
import string
import time


PAIR_EXPIRE = 300      # 5 minutes


class WifiPairManager:

    def __init__(self):
        self._pair_code = None
        self._expire = 0

    # -------------------------------------------------

    def get_ip(self):

        candidates = []

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            candidates.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass

        try:
            ip = socket.gethostbyname(socket.gethostname())
            candidates.append(ip)
        except Exception:
            pass

        try:
            infos = socket.getaddrinfo(
                socket.gethostname(),
                None,
                socket.AF_INET
            )

            for info in infos:
                candidates.append(info[4][0])

        except Exception:
            pass

        for ip in candidates:

            if ip.startswith("127."):
                continue

            if ip.startswith("169.254"):
                continue

            return ip

        return "127.0.0.1"

    # -------------------------------------------------

    def new_pair_code(self):

        chars = string.digits

        self._pair_code = "".join(
            secrets.choice(chars)
            for _ in range(6)
        )

        self._expire = time.time() + PAIR_EXPIRE

        return self._pair_code

    # -------------------------------------------------

    def verify(self, code: str):

        if time.time() > self._expire:
            return False

        return str(code).strip() == str(self._pair_code)

    # -------------------------------------------------

    @property
    def current_code(self):
        return self._pair_code

    @property
    def ip(self):
        return self.get_ip()