import asyncio
import json
import logging
import random
import struct
import time
import websockets

from aiortc import RTCPeerConnection, RTCSessionDescription
from crsf_parser import CRSFParser, PacketValidationStatus

JANUS_WS = "ws://localhost:8188/janus"
ROOM_ID = 1234
USERNAME = f"pult{random.randint(100,999)}"
DISPLAY = "PULT"


def strip_rtp(pkt: bytes) -> bytes:
    return pkt[12:] if len(pkt) > 12 else pkt


parser = CRSFParser(
    lambda frame, status: (
        (
            logging.info("RX channels: %s", frame.payload.channels),
            logging.info("RX raw: %s", frame.raw.hex(" ")),
        )
        if status == PacketValidationStatus.VALID
        else None
    )
)


async def run():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("aioice").setLevel(logging.WARNING)

    async with websockets.connect(JANUS_WS, subprotocols=["janus-protocol"]) as ws:

        await ws.send(json.dumps({"janus": "create", "transaction": "a"}))
        sid = (json.loads(await ws.recv()))["data"]["id"]

        await ws.send(
            json.dumps(
                {
                    "janus": "attach",
                    "plugin": "janus.plugin.textroom",
                    "session_id": sid,
                    "transaction": "b",
                }
            )
        )
        hid = (json.loads(await ws.recv()))["data"]["id"]

        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "session_id": sid,
                    "handle_id": hid,
                    "transaction": "c",
                    "body": {"request": "setup"},
                }
            )
        )
        await ws.recv()

        while True:
            ev = json.loads(await ws.recv())
            if ev.get("janus") == "event" and "jsep" in ev:
                offer = ev["jsep"]
                break

        pc = RTCPeerConnection()

        @pc.on("datachannel")
        def on_dc(dc):
            logging.info("DC open, joining room")

            @dc.on("open")
            def _(_):
                dc.send(
                    json.dumps(
                        {
                            "textroom": "join",
                            "transaction": "j2",
                            "room": ROOM_ID,
                            "username": USERNAME,
                            "display": DISPLAY,
                            "datatype": "binary",
                        }
                    )
                )

            @dc.on("message")
            def on_msg(msg):
                if isinstance(msg, (bytes, bytearray)):
                    if len(msg) >= 8:
                        remote_ts = struct.unpack_from("!I", msg, 4)[0]
                        now_ms = int(time.time() * 1000) & 0xFFFFFFFF
                        latency = (now_ms - remote_ts) & 0xFFFFFFFF
                        logging.info("Latency: %d ms", latency)
                    parser.parse_stream(strip_rtp(msg))
                else:
                    logging.info("RX text: %s", msg)

        # 5. SDP answer
        await pc.setRemoteDescription(
            RTCSessionDescription(offer["sdp"], offer["type"])
        )
        await pc.setLocalDescription(await pc.createAnswer())
        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "session_id": sid,
                    "handle_id": hid,
                    "transaction": "d",
                    "body": {"request": "configure"},
                    "jsep": {
                        "type": pc.localDescription.type,
                        "sdp": pc.localDescription.sdp,
                    },
                }
            )
        )

        await ws.recv()
        await ws.recv()
        logging.info("Pult ready, awaiting packets…")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(run())
