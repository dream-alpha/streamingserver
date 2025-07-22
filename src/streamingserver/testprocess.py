import subprocess

try:
    server_proc = subprocess.Popen([  # pylint: disable=consider-using-with
        "chroot", "/data/ubuntu", "/root/venv/bin/python", "/root/git/dev-streamingserver/src/streamingserver/main.py", "--server"
    ])
    print("Started streamingserver.py in chroot /data/ubuntu using venv with --server")
except Exception as e:
    print("Failed to start streamingserver.py in chroot with venv: %s", e)
