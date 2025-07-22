import socket
import json
s = socket.socket()
s.connect(('192.168.1.99', 5000))
msg = json.dumps({"command": "stop", "args": []}) + '\n'
s.sendall(msg.encode('utf-8'))
s.close()
