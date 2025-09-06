#!/usr/bin/env python3
# Copyright (c) 2018 - 2025 dream-alpha
# License: GNU General Public License v3.0 (see LICENSE file for details)

import time
import threading
import traceback
import argparse

from hls_recorder import HLS_Recorder
from socket_server import SocketServer, CommandHandler
from debug import get_logger

logger = get_logger(__file__)


def main():
    """
    Main function to run the HLS recorder and command server.

    This function parses command-line arguments, initializes the HLS_Recorder,
    and starts a socket server to listen for commands. It can also start a
    recording immediately if a channel is provided via arguments.
    """
    logger.debug("HLS Recorder")

    parser = argparse.ArgumentParser(description="HLS Recorder")
    parser.add_argument('--rec_file', type=str, default='pluto.ts', help='File name for recording file (default: pluto.ts)')
    parser.add_argument('--channel', type=str, help='Channel ID')
    parser.add_argument('--show_ads', action='store_true', help='Whether to show ads (default: False)')
    args = parser.parse_args()

    recorder = HLS_Recorder()
    socketserver = None
    try:
        # Start socket server in background thread
        HOST, PORT = "0.0.0.0", 5000
        socketserver = SocketServer((HOST, PORT), CommandHandler, recorder)
        recorder.socketserver = socketserver
        socketserver_thread = threading.Thread(target=socketserver.serve_forever, daemon=True)
        socketserver_thread.start()
        logger.debug("Command socket server running on %s:%s", HOST, PORT)
        logger.debug("Ready for commands. Use 'start', 'stop' via socket.")
        if args.channel:
            logger.debug("Press Ctrl+C to exit.")
            recorder.start(args.channel, args.rec_file, args.show_ads)
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.debug("Interrupted by user")
        recorder.stop()
        if socketserver is not None:
            socketserver.shutdown()
            socketserver.server_close()
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        traceback.print_exc()
        recorder.stop()
        if socketserver is not None:
            socketserver.shutdown()
            socketserver.server_close()

    logger.debug("Recording session ended")


if __name__ == "__main__":
    main()
