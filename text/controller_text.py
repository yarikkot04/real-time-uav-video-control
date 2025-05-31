import asyncio
import json
import uuid
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.exceptions import InvalidStateError

JANUS_WS = "ws://localhost:8188"
ROOM_ID = 1234
USERNAME = "pult"


async def connect_to_janus():

    async with websockets.connect(JANUS_WS, subprotocols=["janus-protocol"]) as ws:
        print("[PULT] Connected to Janus WebSocket")

        await ws.send(json.dumps({"janus": "create", "transaction": str(uuid.uuid4())}))
        session_id = json.loads(await ws.recv())["data"]["id"]
        print(f"[PULT] Session created: {session_id}")

        transaction_id = str(uuid.uuid4())
        await ws.send(
            json.dumps(
                {
                    "janus": "attach",
                    "plugin": "janus.plugin.textroom",
                    "session_id": session_id,
                    "transaction": transaction_id,
                }
            )
        )

        handle_id = json.loads(await ws.recv())["data"]["id"]
        print(f"[PULT] Plugin attached: {handle_id}")

        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "body": {"request": "setup"},
                    "session_id": session_id,
                    "handle_id": handle_id,
                    "transaction": str(uuid.uuid4()),
                }
            )
        )

        jsep = None
        while True:
            msg = json.loads(await ws.recv())
            if "jsep" in msg:
                jsep = msg["jsep"]
                break

        pc = RTCPeerConnection()
        data_channel = pc.createDataChannel("JanusDataChannel")
        data_channel.last_participants = []

        @data_channel.on("open")
        def on_open():
            print("[PULT] DataChannel opened")
            asyncio.create_task(join_and_wait_for_drone(data_channel))

        @data_channel.on("message")
        def on_message(msg):
            try:
                data = json.loads(msg)
                print(data)
                if data.get("textroom") == "message":
                    print(f"[{data['from']}]: {data['text']}")

                elif data.get("textroom") == "success":
                    print("[PULT] Successfully joined the room")
                    if "participants" in data:
                        data_channel.last_participants = data["participants"]
            except Exception:
                print(f"[PULT][MSG] {msg}")

        await pc.setRemoteDescription(RTCSessionDescription(jsep["sdp"], jsep["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "body": {},
                    "jsep": {"type": "answer", "sdp": pc.localDescription.sdp},
                    "session_id": session_id,
                    "handle_id": handle_id,
                    "transaction": str(uuid.uuid4()),
                }
            )
        )

        while True:
            await asyncio.sleep(1)


async def join_and_wait_for_drone(channel):

    join_msg = {
        "textroom": "join",
        "room": ROOM_ID,
        "username": USERNAME,
        "display": "Pult",
        "transaction": str(uuid.uuid4()),
    }
    channel.send(json.dumps(join_msg))

    print("[PULT] Sent JOIN, waiting for drone...")

    for _ in range(10):
        await asyncio.sleep(1)
        if any(p.get("username") == "drone" for p in channel.last_participants):
            print("[PULT] Drone has joined the room!")
            break
    else:
        print("[PULT] ⚠️ Drone did not join in time")

    asyncio.create_task(read_and_send(channel))


async def read_and_send(channel):
    while True:
        msg = await asyncio.get_event_loop().run_in_executor(None, input, "")
        if not msg.strip():
            continue

        payload = {
            "textroom": "message",
            "transaction": str(uuid.uuid4()),
            "room": ROOM_ID,
            "text": msg,
        }
        try:
            channel.send(json.dumps(payload))
            print(f"[PULT] Sent: {msg}")
        except InvalidStateError:
            print("[ERROR] DataChannel not ready")


asyncio.run(connect_to_janus())
