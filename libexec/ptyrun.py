import os, pty, sys, time, select, signal
cmd = sys.argv[1:]
dur = float(os.environ.get("PTY_SECONDS", "30"))
# keystrokes: "delay:bytes" separated by ";;"
keys = []
for spec in os.environ.get("PTY_KEYS", "").split(";;"):
    if spec.strip():
        d, _, k = spec.partition(":")
        keys.append((float(d), k.encode().decode("unicode_escape").encode()))
pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.environ["COLUMNS"] = "120"; os.environ["LINES"] = "40"
    os.execvp(cmd[0], cmd)
os.set_blocking(fd, False)
buf = b""; t0 = time.time(); sent = 0
while time.time() - t0 < dur:
    el = time.time() - t0
    while sent < len(keys) and keys[sent][0] <= el:
        os.write(fd, keys[sent][1]); sent += 1
    r, _, _ = select.select([fd], [], [], 0.3)
    if r:
        try:
            d = os.read(fd, 65536)
            if not d: break
            buf += d
        except OSError:
            break
for sig in (signal.SIGTERM, signal.SIGKILL):
    try: os.kill(pid, sig)
    except Exception: pass
    time.sleep(0.7)
try: os.waitpid(pid, os.WNOHANG)
except Exception: pass
open(os.environ.get("PTY_OUT", "/tmp/ocp-probe/i/tui.out"), "wb").write(buf)
print(f"captured {len(buf)} bytes, sent {sent} keystrokes")
