# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import json
import socketserver
from plutotv_utils import update_channel_epg_cache
from debug import get_logger

logger = get_logger(__file__)


class CommandHandler(socketserver.BaseRequestHandler):
    def handle(self):
        logger.debug("Connection established with %s", self.client_address)

        # Send a 'ready' message to the client upon connection
        ready_message = {"command": "ready", "args": []}
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
                    cmd = req.get("command", "")
                    if cmd == "start":
                        args = tuple(req.get("args", []))
                        if len(args) == 4:
                            channel_uri, rec_file, show_ads, buffering = args
                            self.server.recorder.start(channel_uri, rec_file, show_ads, buffering)
                        else:
                            logger.error("Invalid number of arguments for 'start' command: %s", args)
                    elif cmd == "stop":
                        self.server.recorder.stop()
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
        update_channel_epg_cache()

    def broadcast(self, message):
        # Broadcast message to all connected clients
        logger.debug("broadcast: %s", message)
        data = (json.dumps(message) + '\n').encode()
        for client in self.clients:
            try:
                client.sendall(data)
            except Exception as e:
                logger.error("Error sending to client: %s", e)
