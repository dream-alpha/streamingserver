import json
import socketserver
from m3u8_playlist_utils import get_playlist_groups
from plutotv_utils import create_playlist_and_epg
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
                        args = req.get("args", [])
                        channel_uri = args[0]
                        rec_file = args[1]
                        self.server.recorder.start(channel_uri, rec_file)
                    elif cmd == "stop":
                        self.server.recorder.stop()
                    elif cmd == "get_playlist":
                        create_playlist_and_epg()
                        playlist = get_playlist_groups("/root/plugins/streamingserver/data/plutotv-playlist.m3u8")
                        response = {"command": "get_playlist", "args": [playlist]}
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    else:
                        logger.debug("❌ Unknown command: %s", cmd)
                except Exception as e:
                    logger.debug("❌ Error handling command: %s", e)
        except Exception as e:
            logger.debug("❌ Connection error: %s", e)


class SocketServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, recorder):
        super().__init__(server_address, handler_class)
        self.recorder = recorder
        self.clients = []  # List of active client sockets
        logger.info("RecorderSocketServer initialized at %s", server_address)

    def broadcast(self, message):
        # Broadcast message to all connected clients
        logger.debug("broadcast: %s", message)
        data = (json.dumps(message) + '\n').encode()
        for client in self.clients:
            try:
                client.sendall(data)
            except Exception as e:
                logger.debug("Error sending to client: %s", e)
