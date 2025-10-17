# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import json
import time
from pathlib import Path
import socketserver

try:
    from provider_manager import ProviderManager
    from resolver_manager import ResolverManager
    from stream_recorder import StreamRecorder
    from debug import get_logger
except ImportError:
    from .provider_manager import ProviderManager
    from .resolver_manager import ResolverManager
    from .stream_recorder import StreamRecorder
    from .debug import get_logger

logger = get_logger(__file__)


class CommandHandler(socketserver.BaseRequestHandler):
    def __init__(self, request, client_address, server):
        self.provider_manager = ProviderManager()
        self.resolver_manager = ResolverManager()
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
                        data_dir = "/tmp/data"
                    if provider_id:
                        data_dir = Path(data_dir) / provider_id
                        data_dir.mkdir(parents=True, exist_ok=True)
                        logger.info("data_dir: %s", data_dir)

                    if cmd == "start":
                        logger.info("Starting recording with args: %s", args)
                        if self.server.recorder is None:
                            self.server.recorder = StreamRecorder()
                            self.server.recorder.server = self.server

                        resolver_instance = self.resolver_manager.get_resolver(provider_id)

                        if resolver_instance and hasattr(resolver_instance, 'resolve_url'):
                            logger.info("Resolving URL for recording: %s", args.get("url", ""))
                            resolve_result = resolver_instance.resolve_url(args)

                            # Add metadata to resolve_result
                            if resolve_result and resolve_result.get("resolved"):
                                for key in args:
                                    if key not in resolve_result:
                                        resolve_result[key] = args[key]
                                resolve_result["socketserver"] = self.server
                                logger.info("Resolution successful: %s", resolve_result)

                                self.server.recorder.record_stream(resolve_result)
                            else:
                                # Resolution failed
                                logger.warning("URL resolution failed, stopping here.")

                    elif cmd == "stop":
                        logger.info("Stopping recording")
                        try:
                            if self.server.recorder is not None:
                                self.server.recorder.stop()
                        except Exception as e:
                            logger.error("Error stopping recording: %s", e)
                            error_response = ["stop", {"status": "error", "message": f"Failed to stop recording: {e}"}]
                            self.request.sendall((json.dumps(error_response) + '\n').encode('utf-8'))

                    elif cmd in {"get_providers", "get_categories", "get_media_items"}:
                        try:
                            provider_instance = None
                            if provider_id and data_dir:
                                provider_instance = self.provider_manager.get_provider(
                                    provider_id,
                                    data_dir
                                )

                            if cmd == "get_providers":
                                try:
                                    providers_data = self.provider_manager.get_providers()
                                    response = ["get_providers", {"data": providers_data}]
                                    logger.info("Sending providers: %s", response)
                                    response_json = json.dumps(response, ensure_ascii=False)
                                    self.request.sendall((response_json + '\n').encode('utf-8'))
                                    # Ensure data is flushed
                                    time.sleep(0.01)  # Small delay to ensure transmission
                                    logger.debug("Providers response sent successfully")
                                except Exception as e:
                                    logger.error("Error sending providers response: %s", e)
                                    error_response = ["error", {"message": f"Failed to get providers: {e}"}]
                                    self.request.sendall((json.dumps(error_response) + '\n').encode('utf-8'))

                            elif provider_instance:
                                if cmd == "get_categories":
                                    try:
                                        categories = provider_instance.get_categories()
                                        response = ["get_categories", {"data": categories}]
                                        response_json = json.dumps(response, ensure_ascii=False)
                                        self.request.sendall((response_json + '\n').encode('utf-8'))
                                        time.sleep(0.01)  # Small delay to ensure transmission
                                        logger.debug("Categories response sent successfully")
                                    except Exception as e:
                                        logger.error("Error getting categories: %s", e)
                                        error_response = ["error", {"message": f"Failed to get categories: {e}"}]
                                        self.request.sendall((json.dumps(error_response) + '\n').encode('utf-8'))

                                elif cmd == "get_media_items":
                                    try:
                                        category = args.get("category", None)
                                        media_items = provider_instance.get_media_items(category)
                                        response = ["get_media_items", {"data": media_items}]
                                        logger.info("Sending %d media_items", len(media_items) if media_items else 0)

                                        # Test JSON serialization first
                                        response_json = json.dumps(response, ensure_ascii=False)
                                        response_size = len(response_json.encode('utf-8'))
                                        logger.info("Response size: %d bytes", response_size)

                                        # Send response
                                        self.request.sendall((response_json + '\n').encode('utf-8'))
                                        time.sleep(0.01)  # Small delay to ensure transmission
                                        logger.info("Response sent successfully")
                                    except Exception as e:
                                        logger.error("Error getting media items: %s", e)
                                        error_response = ["error", {"message": f"Failed to get media items: {e}"}]
                                        self.request.sendall((json.dumps(error_response) + '\n').encode('utf-8'))
                            elif cmd in {"get_categories", "get_media_items"}:
                                # Provider instance not available
                                logger.warning("Provider instance not available for command: %s", cmd)
                                error_response = ["error", {"message": f"Provider not available for {cmd}"}]
                                self.request.sendall((json.dumps(error_response) + '\n').encode('utf-8'))
                        except Exception as e:
                            logger.error("Error processing command %s: %s", cmd, e)
                            error_response = ["error", {"message": f"Command processing failed: {e}"}]
                            try:
                                self.request.sendall((json.dumps(error_response) + '\n').encode('utf-8'))
                            except Exception as send_error:
                                logger.error("Failed to send error response: %s", send_error)
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
