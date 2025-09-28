# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import os
import json
from pathlib import Path
import socketserver
import shutil
from provider import Provider
from config import config
from version import ID
from debug import get_logger

logger = get_logger(__file__)


class CommandHandler(socketserver.BaseRequestHandler):
    def __init__(self, request, client_address, server):
        self.provider_factory = Provider()
        super().__init__(request, client_address, server)

    def get_providers(self, data_dir):
        providers = []
        with open(data_dir / 'providers.json', 'r', encoding="utf-8") as f:
            providers = json.load(f)
        return providers

    def handle(self):
        logger.debug("Connection established with %s", self.client_address)

        data_dirs = [
            config.plugins.streamingcockpit.data_dir.value,
            "/root/plugins/streamingserver/data"
        ]
        logger.info("data_dirs: %s", data_dirs)
        for adir in data_dirs:
            if adir:
                self.data_dir = Path(adir)
                if self.data_dir.exists():
                    break
        logger.info("Using data directory: %s", self.data_dir)
        self.data_dir = self.data_dir / ID
        os.makedirs(self.data_dir, exist_ok=True)

        # copy default providers.json if not present in user data dir
        if not (self.data_dir / "providers.json").exists():
            logger.info("Copying default providers.json to %s", self.data_dir)
            shutil.copyfile("/root/plugins/streamingserver/data/providers.json", self.data_dir / "providers.json")

        # Send a 'ready' message to the client upon connection
        ready_message = ["ready", {}]
        self.request.sendall((json.dumps(ready_message) + '\n').encode())

        # Register client socket
        if hasattr(self.server, 'clients'):
            self.server.clients.append(self.request)

        try:
            while True:
                data = self.request.recv(4096)
                if not data:
                    logger.debug("Connection closed by client %s", self.client_address)
                    # Unregister client socket
                    if hasattr(self.server, 'clients') and self.request in self.server.clients:
                        self.server.clients.remove(self.request)
                    break
                try:
                    req = json.loads(data.strip().decode())
                    logger.debug("socket server received: %s", req)
                    cmd = req[0]
                    args = req[1] if len(req) > 1 else {}
                    if cmd == "start":
                        logger.info("Starting recording with args: %s", args)
                        self.server.recorder.start(
                            args.get("url", "none"),
                            args.get("rec_dir", "/tmp"),
                            args.get("show_ads", False),
                            args.get("buffering", 5),
                        )
                    elif cmd == "stop":
                        logger.info("Stopping recording")
                        self.server.recorder.stop()
                    elif cmd == "get_providers":
                        response = ["get_providers", {"data": self.get_providers(self.data_dir)}]
                        logger.info("Sending providers: %s", response)
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    elif cmd == "get_categories":
                        categories = []
                        provider = args.get("provider", None)
                        if provider:
                            provider_instance = self.provider_factory.get_provider(
                                provider.get("name", ""),
                                self.data_dir / provider.get("path", "")
                            )
                            if provider_instance:
                                categories = provider_instance.get_categories()
                        response = ["get_categories", {"data": categories}]
                        logger.info("Sending categories: %s", categories)
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    elif cmd == "get_channels":
                        channels = []
                        provider = args.get("provider", None)
                        category = args.get("category", None)
                        if provider and category:
                            provider_instance = self.provider_factory.get_provider(
                                provider.get("name", ""),
                                self.data_dir / provider.get("path", "")
                            )
                            if provider_instance:
                                channels = provider_instance.get_channels(category)
                        response = ["get_channels", {"data": channels}]
                        logger.info("Sending channels: %s", channels)
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    else:
                        logger.error("Unknown command: %s", cmd)
                except Exception as e:
                    logger.error("Error handling command: %s", e)
        except Exception as e:
            logger.error("Connection error: %s", e)


class SocketServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, recorder):
        super().__init__(server_address, handler_class)
        self.recorder = recorder
        self.clients = []  # List of active client sockets
        logger.info("RecorderSocketServer initialized at %s", server_address)

    def broadcast(self, message):
        # Broadcast message to all connected clients
        logger.info("broadcast: %s", message)
        data = (json.dumps(message) + '\n').encode()
        for client in self.clients:
            try:
                client.sendall(data)
            except Exception as e:
                logger.error("Error sending to client: %s", e)
