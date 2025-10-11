#!/usr/bin/env python3
# Copyright (C) 2018-2025 by dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import sys
import signal
import time
import threading
import traceback

from socket_server import SocketServer, CommandHandler
from debug import get_logger

logger = get_logger(__file__)


def shutdown_handler(signum, _frame):
    logger.debug(f"Received signal {signum}, shutting down...")
    global socketserver  # pylint: disable=global-variable-not-assigned
    if 'socketserver' in globals() and socketserver is not None:
        # Let the socket server handle stopping any active recorder
        socketserver.shutdown()
        socketserver.server_close()
    sys.exit(0)


def main():
    """
    Main function to run the command server.

    This function starts a socket server to listen for commands.
    """
    logger.info("*" * 70)
    logger.info("Streaming Server")
    logger.info("*" * 70)
    # Ensure shutdown handler is registered for SIGTERM and SIGINT
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    global socketserver  # pylint: disable=global-variable-undefined
    socketserver = None

    try:
        # Start socket server in background thread
        HOST, PORT = "0.0.0.0", 5000
        socketserver = SocketServer((HOST, PORT), CommandHandler)
        socketserver_thread = threading.Thread(target=socketserver.serve_forever, daemon=True)
        socketserver_thread.start()
        logger.debug("Command socket server running on %s:%s", HOST, PORT)
        logger.debug("Ready for commands. Use 'start', 'stop' via socket.")
        logger.debug("Press Ctrl+C to exit.")
        while True:
            time.sleep(1)

    except Exception as e:
        logger.error("Unexpected error: %s", e)
        traceback.print_exc()
        shutdown_handler(None, None)

    logger.debug("Recording session ended")


if __name__ == "__main__":
    main()
