#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

"""
Socket Client for Async Message Handling

A minimal socket client that invokes handle_message when messages arrive from the server.
"""

import socket
import struct
import json
import threading
import time
from debug import get_logger

logger = get_logger(__file__)


def send_length_prefixed_message(socket_conn, message):
    """Send a JSON message with length prefix (standard approach for large TCP data)"""
    json_data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    length_prefix = struct.pack('>I', len(json_data))  # 4-byte big-endian length
    socket_conn.sendall(length_prefix + json_data)


def recv_length_prefixed_message(socket_conn):
    """Receive a length-prefixed JSON message"""
    # First, receive the 4-byte length prefix
    length_data = b''
    while len(length_data) < 4:
        chunk = socket_conn.recv(4 - len(length_data))
        if not chunk:
            raise ConnectionError("Connection closed while reading length prefix")
        length_data += chunk

    # Unpack the length
    message_length = struct.unpack('>I', length_data)[0]

    # Now receive the exact message
    json_data = b''
    while len(json_data) < message_length:
        chunk = socket_conn.recv(message_length - len(json_data))
        if not chunk:
            raise ConnectionError("Connection closed while reading message")
        json_data += chunk

    # Parse and return the JSON
    return json.loads(json_data.decode('utf-8'))


class SocketClient:
    """Simple socket client with async message handling"""

    def __init__(self, host='127.0.0.1', port=5000, timeout=30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.connected = False
        self.running = False
        self.listen_thread = None

    def connect(self):
        """Connect to the streaming server"""
        try:
            logger.info("Connecting to %s:%d", self.host, self.port)
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)
            self.socket.connect((self.host, self.port))
            self.connected = True

            # Start listening for messages
            self.running = True
            self.listen_thread = threading.Thread(target=self._listen_for_messages, daemon=True)
            self.listen_thread.start()

            logger.info("Connected successfully")
            return True

        except Exception as e:
            logger.error("Connection failed: %s", e)
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from server gracefully"""
        self.running = False

        if self.socket:
            try:
                # Send a final message to signal clean disconnect (optional)
                # This helps the server know we're disconnecting intentionally
                time.sleep(0.1)  # Small delay to let any pending messages process
            except Exception:
                pass

            try:
                # Shutdown the socket for both reading and writing
                self.socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass

            try:
                # Close the socket
                self.socket.close()
            except Exception:
                pass

            self.socket = None

        self.connected = False

        if self.listen_thread and self.listen_thread.is_alive():
            self.listen_thread.join(timeout=3.0)  # Increased timeout

        logger.info("Disconnected from server")

    def send_command(self, command, args=None):
        """Send a command to the server"""
        if not self.connected:
            logger.error("Not connected to server")
            return False

        if args is None:
            args = {}

        message = [command, args]

        try:
            send_length_prefixed_message(self.socket, message)
            logger.info("Sent command: %s", command)
            return True

        except Exception as e:
            logger.error("Failed to send command %s: %s", command, e)
            return False

    def _listen_for_messages(self):
        """Listen for incoming length-prefixed messages in a separate thread"""
        while self.running and self.connected:
            try:
                # Receive length-prefixed message
                message = recv_length_prefixed_message(self.socket)

                # Call the handle_message method asynchronously
                threading.Thread(
                    target=self.handle_message,
                    args=(message,),
                    daemon=True
                ).start()

            except socket.timeout:
                continue  # Continue listening
            except ConnectionResetError:
                if self.running:
                    logger.warning("Connection reset by server")
                break
            except Exception as e:
                if self.running:  # Only log if we're supposed to be running
                    logger.error("Error receiving message: %s", e)
                break

        self.connected = False

    def handle_message(self, message):
        """Handle incoming message - OVERRIDE THIS METHOD in subclasses"""
        logger.info("Received message: %s", message)
        # Default implementation does nothing - subclasses should override this

    def wait_for_response(self, timeout=None):
        """Wait for any response message (utility method)"""
        if timeout is None:
            timeout = self.timeout

        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.connected:
                return False
            time.sleep(0.1)
        return True
