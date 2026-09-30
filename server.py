import asyncio
import json
import os
import pathlib
import sys

from aiohttp import WSMsgType, web

ROOT = pathlib.Path(__file__).parent
PORT = int(os.environ.get("PORT", 0))
if not PORT:
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

agent_ws = None
agent_pin = None
clients = set()
pending = {}


async def index(_request):
    return web.FileResponse(ROOT / "index.html")


async def health(_request):
    return web.json_response({"ok": True, "agent": agent_ws is not None})


async def broadcast(obj):
    dead = []
    for c in list(clients):
        try:
            await c.send_json(obj)
        except Exception:
            dead.append(c)
    for c in dead:
        clients.discard(c)


async def ws_agent(request):
    global agent_ws, agent_pin
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    if agent_ws is not None and not agent_ws.closed:
        await ws.send_json({"type": "error", "error": "agent already connected"})
        await ws.close()
        return ws
    agent_pin = request.query.get("pin", "")
    agent_ws = ws
    print("agent connected")
    await broadcast({"type": "status", "agent": True})
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if data.get("type") == "result":
                cid = data.get("id")
                c = pending.pop(cid, None)
                if c is not None and not c.closed:
                    try:
                        await c.send_json(data)
                    except Exception:
                        pass
    agent_ws = None
    agent_pin = None
    print("agent disconnected")
    await broadcast({"type": "status", "agent": False})
    return ws


async def ws_client(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    pin = request.query.get("pin", "")
    if agent_ws is None:
        await ws.send_json({"type": "status", "agent": False})
    elif pin != (agent_pin or ""):
        await ws.send_json({"type": "error", "error": "wrong pin"})
        await ws.close()
        return ws
    else:
        await ws.send_json({"type": "status", "agent": True})
    clients.add(ws)
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if data.get("type") == "command" and agent_ws is not None:
                cid = data.get("id")
                if cid is not None:
                    pending[cid] = ws
                try:
                    await agent_ws.send_json(data)
                except Exception:
                    pending.pop(cid, None)
                    await ws.send_json({"type": "result", "id": cid, "ok": False, "error": "agent send failed"})
            elif data.get("type") == "ping":
                await ws.send_json({"type": "pong", "agent": agent_ws is not None})
    clients.discard(ws)
    return ws


def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/agent", ws_agent)
    app.router.add_get("/client", ws_client)
    print(f"listening on :{PORT}")
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
