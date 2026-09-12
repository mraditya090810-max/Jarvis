"""
dashboard/discovery.py

Automatic LAN discovery for JARVIS.

PC broadcasts its presence every 2 seconds.
Phones can automatically discover the PC without QR codes.
"""

import socket
import threading
import json
import time

DISCOVERY_PORT = 54545
BROADCAST_INTERVAL = 2


class DiscoveryService:
    def __init__(self, server_ip, server_port, device_name="JARVIS-PC"):
        self.server_ip = server_ip
        self.server_port = server_port
        self.device_name = device_name
        self.running = False

    def start(self):
        if self.running:
            return

        self.running = True

        threading.Thread(
            target=self._broadcast_loop,
            daemon=True
        ).start()

        print("[Discovery] Started")

    def stop(self):
        self.running = False
        print("[Discovery] Stopped")

    def _broadcast_loop(self):

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BROADCAST,
            1
        )

        packet = json.dumps({
            "type": "jarvis",
            "name": self.device_name,
            "ip": self.server_ip,
            "port": self.server_port
        }).encode()

        while self.running:

            try:
                sock.sendto(
                    packet,
                    ("255.255.255.255", DISCOVERY_PORT)
                )
            except Exception:
                pass

            time.sleep(BROADCAST_INTERVAL)