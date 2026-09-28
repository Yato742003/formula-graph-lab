"""Adversarial test image only; never copied into the production worker image."""
import json
import os
import socket
import subprocess
import sys
import time

mode = json.load(sys.stdin)["mode"]
if mode == "flood":
    while True:
        sys.stdout.write("x" * 8192)
        sys.stdout.flush()
if mode == "memory":
    block = bytearray(512 * 1024 * 1024)
if mode == "timeout":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    time.sleep(60)

checks = {}
try:
    socket.create_connection(("1.1.1.1", 443), timeout=1).close()
    checks["network_blocked"] = False
except OSError:
    checks["network_blocked"] = True
try:
    socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP).close()
    checks["raw_socket_blocked"] = False
except OSError:
    checks["raw_socket_blocked"] = True
try:
    with open("/opt/worker/overwrite", "w") as file:
        file.write("bad")
    checks["readonly"] = False
except OSError:
    checks["readonly"] = True
try:
    with open("/tmp/fill", "wb") as file:
        for _ in range(20):
            file.write(b"x" * (1024 * 1024))
    checks["scratch_bounded"] = False
except OSError:
    checks["scratch_bounded"] = True
checks["no_secrets"] = not any(k in os.environ for k in (
    "SERVICE_TOKEN", "OPENAI_API_KEY", "NEO4J_PASSWORD", "FGL_RESEARCH_QUEUE_SECRET",
))
checks["unprivileged"] = os.getuid() == 65534
with open("/proc/self/status") as file:
    status = file.read()
checks["seccomp"] = "Seccomp:\t2" in status
checks["no_new_privileges"] = "NoNewPrivs:\t1" in status
checks["no_host_mount"] = not os.path.exists("/var/run/docker.sock")
children = []
try:
    for _ in range(30):
        children.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]))
    checks["pids_bounded"] = False
except OSError:
    checks["pids_bounded"] = True
finally:
    for child in children:
        child.kill()
        child.wait()
print(json.dumps(checks))
