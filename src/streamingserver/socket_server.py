import json
import socketserver
from xml_playlist_utils import get_playlist


# --- Socket server for command control ---
class RecorderCommandHandler(socketserver.BaseRequestHandler):
    def handle(self):
        print(f"Connection established with {self.client_address}")
        # Register client socket
        if hasattr(self.server, 'clients'):
            self.server.clients.append(self.request)
        try:
            while True:
                data = self.request.recv(4096)
                if not data:
                    print(f"Connection closed by client {self.client_address}")
                    # Unregister client socket
                    if hasattr(self.server, 'clients') and self.request in self.server.clients:
                        self.server.clients.remove(self.request)
                    break
                try:
                    req = json.loads(data.strip().decode())
                    print(f"socket server received: {req}")
                    cmd = req.get("command", "")
                    if cmd == "start":
                        args = req.get("args", [])
                        channel_uri = args[0]
                        rec_file = args[1]
                        self.server.recorder.start(channel_uri, rec_file)
                    elif cmd == "stop":
                        self.server.recorder.stop()
                    elif cmd == "get_playlist":
                        playlist = get_playlist("/root/plugins/streamingserver/playlists/de.xml")
                        response = {"command": "get_playlist", "args": [playlist]}
                        self.request.sendall((json.dumps(response) + '\n').encode())
                    else:
                        print(f"❌ Unknown command: {cmd}")
                except Exception as e:
                    print(f"❌ Error handling command: {e}")
        except Exception as e:
            print(f"❌ Connection error: {e}")


class RecorderSocketServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, recorder):
        super().__init__(server_address, handler_class)
        self.recorder = recorder
        self.clients = []  # List of active client sockets

    def broadcast(self, message):
        # Broadcast message to all connected clients
        print(f"broadcast: {message}")
        data = (json.dumps(message) + '\n').encode()
        for client in self.clients:
            try:
                client.sendall(data)
            except Exception as e:
                print(f"Error sending to client: {e}")
