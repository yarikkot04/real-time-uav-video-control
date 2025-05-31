import asyncio
import json
import uuid
import websockets
import time  
from datetime import datetime 
from aiortc import RTCPeerConnection, RTCSessionDescription

JANUS_WS = "ws://localhost:8188"
ROOM_ID = 1234
USERNAME = "drone"


async def connect_to_janus():
    async with websockets.connect(JANUS_WS, subprotocols=["janus-protocol"]) as ws:
        print("[DRONE] Connected to Janus WebSocket")

        create_tx = str(uuid.uuid4())
        await ws.send(json.dumps({"janus": "create", "transaction": create_tx}))
        response = json.loads(await ws.recv())
        session_id = response["data"]["id"]
        print(f"[DRONE] Session created: {session_id}")

        attach_tx = str(uuid.uuid4())

        await ws.send(
            json.dumps(
                {
                    "janus": "attach",
                    "plugin": "janus.plugin.textroom",
                    "session_id": session_id,
                    "transaction": attach_tx,
                }
            )
        )

        response = json.loads(await ws.recv())
        handle_id = response["data"]["id"]
        print(f"[DRONE] Plugin attached: {handle_id}")

        setup_tx = str(uuid.uuid4())
        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "body": {"request": "setup"},
                    "session_id": session_id,
                    "handle_id": handle_id,
                    "transaction": setup_tx,
                }
            )
        )

        jsep = None
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("janus") == "event" and "jsep" in msg:
                jsep = msg["jsep"]
                break

        pc = RTCPeerConnection()
        data_channel = pc.createDataChannel("JanusDataChannel")

        joined = asyncio.Event()

        @data_channel.on("open")
        def on_open():
            print("[DRONE] DataChannel opened, sending join request...")

            join_msg = {
                "textroom": "join",
                "room": ROOM_ID,
                "username": USERNAME,
                "display": "Drone",
                "transaction": str(uuid.uuid4()),
            }
            data_channel.send(json.dumps(join_msg))

        @data_channel.on("message")
        def on_message(raw_msg):
            # Print raw message
            try:
                data = json.loads(raw_msg)
                pretty = json.dumps(data, ensure_ascii=False, indent=3)
                print("[DRONE] Received via DataChannel:", pretty)
            except json.JSONDecodeError:
                print(f"[DRONE] Received via DataChannel: {raw_msg}")
                return

            if data.get("textroom") == "message":
                sender = data.get("from")
                text = data.get("text")

                date_str = data.get("date")
                latency_info = ""
                if date_str:
                    if date_str[-5] in ["+", "-"] and ":" not in date_str[-5:]:
                        date_str = date_str[:-5] + date_str[-5:-2] + ":" + date_str[-2:]
                    dt = datetime.fromisoformat(date_str)
                    sent_ms = dt.timestamp() * 1000
                    now_ms = time.time() * 1000
                    latency_ms = now_ms - sent_ms
                    latency_info = f"  | latency {latency_ms:.2f} ms"

                print(f"[DRONE] Message from [{sender}]: {text}{latency_info}")

            elif data.get("textroom") == "success" and "participants" in data:
                print(
                    f"[DRONE] Successfully joined, current participants: {data['participants']}"
                )
                joined.set()
            elif data.get("textroom") == "event" and data.get("error_code"):
                print(f"[DRONE] TextRoom error {data['error_code']}: {data['error']}")
            elif data.get("textroom") == "leave":
                print(f"[DRONE] Participant left the room: {data.get('username')}")

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
        print("[DRONE] WebRTC answer sent, awaiting join confirmation...")

        try:
            await asyncio.wait_for(joined.wait(), timeout=5.0)
            print("[DRONE] Joined the room, sending greeting message.")

            greeting = {
                "textroom": "message",
                "room": ROOM_ID,
                "text": "Drone online",
                "to": "pult",
                "transaction": str(uuid.uuid4()),
            }
            data_channel.send(json.dumps(greeting))

            print("[DRONE] Auto response sent.")
        except asyncio.TimeoutError:
            print(
                "[DRONE] Join confirmation not received in time, cancelling auto response"
            )

        while True:
            await asyncio.sleep(1)


asyncio.run(connect_to_janus())
