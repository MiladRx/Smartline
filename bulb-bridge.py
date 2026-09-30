"""Local bridge: controls the Smartline/Proline BLE bulb over Bluetooth and exposes
a small HTTP API on http://127.0.0.1:8138 so the web UI can use it instead of
Web Bluetooth (whose GATT reads are broken on some Windows setups).

Run with:  python bulb-bridge.py
"""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bleak import BleakClient
from Crypto.Cipher import AES

MAC = "A4:C1:38:36:D0:B2"
SVC = "00010203-0405-0607-0809-0a0b0c0d1910"
STATUS = "00010203-0405-0607-0809-0a0b0c0d1911"
COMMAND = "00010203-0405-0607-0809-0a0b0c0d1912"
PAIR = "00010203-0405-0607-0809-0a0b0c0d1914"
NAME = b"BLE MESH"
PASSWORD = b"123"
VENDOR = b"\x11\x02"
DEFAULT_DEST = 0x00B2
PORT = 8138


def pad16(b):
    b = bytes(b)
    return b[:16] if len(b) >= 16 else b + b"\x00" * (16 - len(b))


def telink_aes(key, block):
    k = bytes(reversed(pad16(key)))
    v = bytes(reversed(pad16(block)))
    return bytes(reversed(AES.new(k, AES.MODE_ECB).encrypt(v)))


def xor_np(n, p):
    return bytes(a ^ b for a, b in zip(pad16(n), pad16(p)))


def build_pair(rnd):
    return b"\x0c" + rnd + telink_aes(rnd, xor_np(NAME, PASSWORD))[:8]


def make_checksum(key, nonce, payload):
    chk = telink_aes(key, pad16(nonce + bytes([len(payload)])))
    for i in range(0, len(payload), 16):
        chk = telink_aes(key, bytes(a ^ b for a, b in zip(chk, pad16(payload[i:i + 16]))))
    return chk


def crypt_payload(key, nonce, payload):
    base = bytearray(pad16(b"\x00" + nonce))
    out = bytearray()
    for i in range(0, len(payload), 16):
        ks = telink_aes(key, bytes(base))
        chunk = payload[i:i + 16]
        out += bytes(a ^ b for a, b in zip(ks[:len(chunk)], chunk))
        base[0] = (base[0] + 1) & 0xFF
    return bytes(out)


def command_packet(key, mac, seq, dest, opcode, params):
    seqb = seq.to_bytes(3, "little")
    nonce = bytes(reversed(mac))[:4] + b"\x01" + seqb
    plain = bytearray(15)
    plain[0:2] = dest.to_bytes(2, "little")
    plain[2] = opcode
    plain[3:5] = VENDOR
    plain[5:5 + len(params)] = params
    return seqb + make_checksum(key, nonce, bytes(plain))[:2] + crypt_payload(key, nonce, bytes(plain))


def decrypt_notification(key, mac, data):
    nonce = bytes(reversed(mac))[:3] + data[:5]
    tag = make_checksum(key, nonce, data[7:])[:2]
    plain = crypt_payload(key, nonce, data[7:])
    return data[:7] + plain, tag == data[5:7]


class Bulb:
    def __init__(self):
        self.client = None
        self.key = None
        self.seq = 0
        self.mac = bytes(int(x, 16) for x in MAC.split(":"))
        self.state = {"connected": False, "on": None, "brightness": None, "tone": None, "meshId": None}

    async def connect(self):
        if self.client is not None and self.client.is_connected:
            return
        self.client = BleakClient(MAC, timeout=25)
        await self.client.connect()
        cr = os.urandom(8)
        await self.client.write_gatt_char(PAIR, build_pair(cr), response=True)
        await asyncio.sleep(0.08)
        try:
            await self.client.write_gatt_char(STATUS, b"\x01", response=True)
        except Exception:
            pass
        resp = b""
        for _ in range(15):
            await asyncio.sleep(0.15)
            try:
                resp = bytes(await self.client.read_gatt_char(PAIR))
            except Exception:
                resp = b""
            if resp:
                break
        if not resp or resp[0] != 0x0D:
            raise RuntimeError("pair failed: " + (resp.hex() if resp else "no response"))
        self.key = telink_aes(xor_np(NAME, PASSWORD), cr + resp[1:9])
        self.seq = 0
        self.state["connected"] = True
        try:
            await self.client.start_notify(STATUS, self._on_notify)
        except Exception:
            try:
                ch = self.client.services.get_characteristic(STATUS)
                ch.obj.add_value_changed(self._on_notify_winrt)
            except Exception:
                pass

    def _on_notify(self, _sender, data):
        self._handle_notify(bytes(data))

    def _on_notify_winrt(self, _sender, args):
        self._handle_notify(bytes(bytearray(args.characteristic_value)))

    def _handle_notify(self, data):
        if not self.key or len(data) < 20:
            return
        try:
            full, ok = decrypt_notification(self.key, self.mac, data)
            if ok and len(full) >= 19:
                self.state["brightness"] = full[13]
                self.state["meshId"] = full[3]
        except Exception:
            pass

    async def command(self, opcode, params, dest):
        if self.client is None or not self.client.is_connected or self.key is None:
            await self.connect()
        pkt = command_packet(self.key, self.mac, self.seq, dest, opcode, params)
        self.seq = (self.seq + 1) & 0xFFFFFF
        await self.client.write_gatt_char(COMMAND, pkt, response=False)
        if opcode == 0xD0 and params:
            self.state["on"] = bool(params[0])
        if opcode == 0xD2 and params:
            self.state["brightness"] = params[0]
        if opcode == 0xE2 and len(params) >= 2 and params[0] == 5:
            self.state["tone"] = params[1]

    async def disconnect(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.key = None
        self.state["connected"] = False


bulb = Bulb()
loop = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path == "/ping":
            self._send(200, {"ok": True, "device": MAC, "name": "BLE MESH bulb"})
        elif self.path == "/state":
            self._send(200, bulb.state)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        try:
            if self.path == "/connect":
                fut = asyncio.run_coroutine_threadsafe(bulb.connect(), loop)
                fut.result(timeout=40)
                self._send(200, {"ok": True, "state": bulb.state})
            elif self.path == "/command":
                opcode = int(body.get("opcode"))
                params = bytes(body.get("params", []))
                dest = int(body.get("dest", DEFAULT_DEST))
                fut = asyncio.run_coroutine_threadsafe(bulb.command(opcode, params, dest), loop)
                fut.result(timeout=25)
                self._send(200, {"ok": True, "state": bulb.state})
            elif self.path == "/disconnect":
                fut = asyncio.run_coroutine_threadsafe(bulb.disconnect(), loop)
                fut.result(timeout=10)
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def log_message(self, *args):
        pass


async def amain():
    global loop
    loop = asyncio.get_running_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("=" * 56)
    print(" Smartline bulb bridge is running")
    print(f" http://127.0.0.1:{PORT}")
    print(" Keep this window open. Now use the web app:")
    print(" http://localhost:8137/")
    print("=" * 56)
    while True:
        await asyncio.sleep(3600)


def main():
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
