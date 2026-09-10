"""Serial transport. Flow control is the whole game: these cutters have a
buffer of about a kilobyte and silently mangle the cut if you overrun it."""
from __future__ import annotations

import time

import serial
from serial.tools import list_ports

ESC = "\x1b"
FTDI_VID = 0x0403


def candidate_ports() -> list[dict]:
    out = []
    for p in list_ports.comports():
        if not (p.device.startswith("/dev/cu.") or p.device.startswith("COM")):
            continue
        if "Bluetooth" in p.device or "debug-console" in p.device:
            continue
        out.append(
            {
                "device": p.device,
                "description": p.description or "",
                "manufacturer": p.manufacturer or "",
                "vid": p.vid,
                "pid": p.pid,
                "serial_number": p.serial_number or "",
                "likely_cutter": p.vid == FTDI_VID and not p.serial_number,
            }
        )
    return out


class SerialTransport:
    def __init__(self, port: str, baud: int = 9600, flow: str = "rtscts",
                 timeout: float = 3.0, chunk: int = 64, pace: float = 0.0) -> None:
        self.port, self.baud, self.flow = port, baud, flow
        self.timeout, self.chunk, self.pace = timeout, chunk, pace
        self.ser: serial.Serial | None = None

    def open(self) -> None:
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=max(self.timeout, 30.0),
            rtscts=(self.flow == "rtscts"),
            xonxoff=(self.flow == "xonxoff"),
            dsrdtr=False,
        )
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, text: str, progress=None) -> int:
        assert self.ser, "port not open"
        data = text.encode("ascii", "strict")
        sent = 0
        for i in range(0, len(data), self.chunk):
            part = data[i : i + self.chunk]
            self.ser.write(part)
            self.ser.flush()
            sent += len(part)
            if self.pace:
                time.sleep(self.pace)
            if progress:
                progress(sent, len(data))
        return sent

    def query(self, cmd: str, timeout: float = 3.0) -> str:
        """Send an HPGL output command and read its reply. Causes no motion."""
        assert self.ser, "port not open"
        self.ser.reset_input_buffer()
        self.ser.write(cmd.encode("ascii"))
        self.ser.flush()
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if buf.endswith(b"\r") or buf.endswith(b"\n"):
                    break
            else:
                time.sleep(0.05)
        return buf.decode("ascii", "replace").strip()

    def wait_idle(self, timeout: float = 900.0) -> bool:
        """Block until the cutter has drained its buffer.

        `OA;` is answered only once everything queued ahead of it has been
        executed, so the reply doubles as a completion barrier. Without this the
        port can close mid-cut and the tail of the job is lost.
        """
        return bool(self.query("OA;", timeout=timeout))

    def abort(self) -> None:
        """Best effort: lift the knife and abort graphics."""
        if not (self.ser and self.ser.is_open):
            return
        try:
            self.ser.reset_output_buffer()
            self.ser.write(f"PU;{ESC}.K".encode("ascii"))
            self.ser.flush()
        except Exception:
            pass
