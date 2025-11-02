# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import json
import struct
import socketserver
from socket_manager import SocketManager, send_message
from debug import get_logger

logger = get_logger(__file__)


class CommandHandler(socketserver.BaseRequestHandler):

    def setup(self):
        """Called after __init__ to set up the handler"""
        # Initialize the request handler logic
        self._socket_manager = SocketManager()
        self._socket_manager.server = self.server
        self._socket_manager.request = self.request

    def handle(self):
        logger.debug("Connection established with %s", self.client_address)

        # Register client socket
        if hasattr(self.server, 'clients'):
            self.server.clients.append(self.request)

        # Send a 'ready' message to the client upon connection
        ready_message = ["ready", {}]
        send_message(self.request, ready_message)

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
                    # Handle length-prefixed JSON messages (standard approach for large TCP data)
                    if not hasattr(self, '_recv_buffer'):
                        self._recv_buffer = b''
                        self._expected_length = None

                    # Append new data to buffer
                    self._recv_buffer += data

                    # If we don't know the message length yet, try to read it
                    if self._expected_length is None:
                        if len(self._recv_buffer) >= 4:  # 4 bytes for length prefix
                            # Read length as 32-bit big-endian integer
                            self._expected_length = struct.unpack('>I', self._recv_buffer[:4])[0]
                            self._recv_buffer = self._recv_buffer[4:]  # Remove length prefix

                            # Sanity check for message length (max 100MB)
                            if self._expected_length > 104857600:
                                logger.error("Message too large: %d bytes", self._expected_length)
                                self._recv_buffer = b''
                                self._expected_length = None
                                continue
                        else:
                            continue  # Wait for complete length prefix

                    # Check if we have the complete message
                    if len(self._recv_buffer) >= self._expected_length:
                        # Extract the complete JSON message
                        json_data = self._recv_buffer[:self._expected_length]
                        self._recv_buffer = self._recv_buffer[self._expected_length:]  # Remove processed data
                        self._expected_length = None  # Reset for next message

                        # Parse the JSON
                        try:
                            json_str = json_data.decode('utf-8')
                            req = json.loads(json_str)
                        except (UnicodeDecodeError, json.JSONDecodeError) as e:
                            logger.error("Failed to parse JSON message: %s", e)
                            continue
                    else:
                        continue  # Wait for complete message

                    logger.debug("socket server received: %s", req)

                    self._socket_manager.handle_message(req)

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
        logger.info("Broadcasting to %d clients", len(self.clients))
        for i, client in enumerate(self.clients):
            try:
                logger.debug("Sending to client %d", i)
                send_message(client, message)
                logger.debug("Successfully sent to client %d", i)
            except Exception as e:
                logger.error("Error sending to client %d: %s", i, e)
