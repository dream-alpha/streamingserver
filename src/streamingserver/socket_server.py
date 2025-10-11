# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import json
from pathlib import Path
import socketserver
from provider_manager import ProviderManager
from stream_recorder import StreamRecorder
from debug import get_logger

logger = get_logger(__file__)


class CommandHandler(socketserver.BaseRequestHandler):
    def __init__(self, request, client_address, server):
        self.provider_manager = ProviderManager()
        super().__init__(request, client_address, server)

    def handle(self):
        logger.debug("Connection established with %s", self.client_address)

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
                    logger.info("args: %s", args)
                    provider_id = args.get("provider", {}).get("provider_id", None)
                    data_dir = args.get("data_dir", None)
                    if not data_dir:
                        data_dir = Path.cwd() / "data"
                    else:
                        data_dir = Path(data_dir)
                    data_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("data_dir: %s", data_dir)

                    if cmd == "start":
                        logger.info("Starting recording with args: %s", args)
                        if self.server.recorder is None:
                            self.server.recorder = StreamRecorder()
                            self.server.recorder.server = self.server

                        url = args.get("url", "")

                        # Try to resolve URL if provider is available
                        resolved_url = url
                        auth_tokens = None
                        original_page_url = None
                        all_sources = None

                        provider_instance = self.provider_manager.get_provider(
                            provider_id,
                            data_dir
                        )
                        if provider_instance and hasattr(provider_instance, 'resolve_url'):
                            logger.info("Resolving URL for recording: %s", url)
                            resolution = args.get("resolution", "best")
                            resolve_result = provider_instance.resolve_url(url, resolution)

                            # Handle enhanced result with auth tokens
                            resolved_url = resolve_result.get("streaming_url", url)
                            auth_tokens = resolve_result.get("auth_tokens")
                            original_page_url = resolve_result.get("original_page_url", url)
                            all_sources = resolve_result.get("all_sources")

                            if resolved_url and resolved_url != url:
                                logger.info("Resolved to streaming URL: %s", resolved_url[:100] + "..." if len(resolved_url) > 100 else resolved_url)
                                if auth_tokens:
                                    logger.info("Authentication tokens acquired for protected stream")
                            else:
                                logger.warning("URL resolution failed, using original URL")
                                resolved_url = url
                        else:
                            logger.warning("Provider doesn't support URL resolution, using original URL")

                        self.server.recorder.record_stream(
                            resolved_url,
                            args.get("rec_dir", "/tmp"),
                            args.get("show_ads", False),
                            args.get("buffering", 5),
                            auth_tokens=auth_tokens,
                            original_page_url=original_page_url,
                            all_sources=all_sources,
                        )
                    elif cmd == "stop":
                        logger.info("Stopping recording")
                        if self.server.recorder is not None:
                            self.server.recorder.stop()
                    elif cmd == "get_providers":
                        response = ["get_providers", {"data": self.provider_manager.get_providers()}]
                        logger.info("Sending providers: %s", response)
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    elif cmd == "get_categories":
                        categories = []
                        if provider_id and data_dir:
                            provider_instance = self.provider_manager.get_provider(
                                provider_id,
                                data_dir
                            )
                            if provider_instance:
                                categories = provider_instance.get_categories()
                        response = ["get_categories", {"data": categories}]
                        # logger.info("Sending categories: %s", categories)
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    elif cmd == "get_media_items":
                        channels = []
                        category = args.get("category", None)
                        if provider_id and category and data_dir:
                            provider_instance = self.provider_manager.get_provider(
                                provider_id,
                                data_dir
                            )
                            if provider_instance:
                                channels = provider_instance.get_media_items(category)
                        response = ["get_media_items", {"data": channels}]
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

    def __init__(self, server_address, handler_class):
        super().__init__(server_address, handler_class)
        self.recorder = None
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
