"""Build water_rig.ino for the host with g++ and talk to it in lock-step (virtual time)."""
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKETCH = ROOT / "hardware" / "firmware" / "water_rig" / "water_rig.ino"


def build(out_dir=None):
    gxx = shutil.which("g++")
    if not gxx:
        return None
    out = Path(out_dir or tempfile.mkdtemp()) / "water_rig_host"
    cmd = [gxx, "-std=c++17", "-O1", "-Wall", "-Wno-unused-parameter", "-I", str(HERE / "stubs"),
           "-include", "Arduino.h", "-include", "Servo.h", "-include", "EEPROM.h",
           "-x", "c++", str(SKETCH), "-x", "none", str(HERE / "host_main.cpp"), "-o", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return out, r.stderr


class HostLink:
    """Same interface as SerialLink/SimLink: write · readline · now."""

    def __init__(self, exe, step_ms=50):
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.ms = 0
        self.step = step_ms
        self.queue = []

    def raw(self, text):
        self.p.stdin.write(text + "\n")
        self.p.stdin.flush()

    def write(self, line):
        self.raw(line)

    def advance(self, ms):
        self.raw(f"~{ms}")
        self.ms += ms
        while True:
            out = self.p.stdout.readline()
            if not out or out.strip() == "~END":
                break
            if out.strip():
                self.queue.append(out.strip())

    def readline(self, timeout=0.1):
        end = self.ms + int(timeout * 1000)
        while not self.queue and self.ms < end:
            self.advance(self.step)
        return self.queue.pop(0) if self.queue else None

    def now(self):
        return self.ms / 1000

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)
