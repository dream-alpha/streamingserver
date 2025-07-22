import socket
import json
s = socket.socket()
s.connect(('192.168.1.99', 5000))
msg = json.dumps({"command": "start", "args": ["630348a54c48ce00077eb6c7", "/media/hdd/movie/pluto.ts"]}) + '\n'
s.sendall(msg.encode('utf-8'))
s.close()
