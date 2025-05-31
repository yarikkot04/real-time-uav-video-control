import sys
import asyncio
import json
import logging
import random
import struct
import websockets
import time

from aiortc import RTCPeerConnection, RTCSessionDescription
from crsf_parser.handling import crsf_build_frame
from crsf_parser.payloads import PacketsTypes

JANUS_WS = "ws://localhost:8188/janus"
ROOM_ID = 1234
USERNAME = "drone"
DISPLAY = "DRONE"

PT = 96
SSRC = random.getrandbits(32)
SEQ = 0


def make_rtp(payload: bytes, seq: int, ts_ms: int) -> bytes:
    header = struct.pack(
        "!BBHII",
        0x80,
        PT,
        seq,
        ts_ms & 0xFFFFFFFF,
        SSRC,
    )
    return header + payload


cli_vals = [int(a) & 0x7FF for a in sys.argv[1:17]]
use_cli = bool(cli_vals)


def next_channels():
    if use_cli:
        return cli_vals + [992] * (16 - len(cli_vals))
    return [random.randint(992, 1500) for _ in range(16)]


async def run():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("aioice").setLevel(logging.WARNING)

    async with websockets.connect(JANUS_WS, subprotocols=["janus-protocol"]) as ws:

        await ws.send(json.dumps({"janus": "create", "transaction": "t1"}))
        sid = (json.loads(await ws.recv()))["data"]["id"]

        await ws.send(
            json.dumps(
                {
                    "janus": "attach",
                    "plugin": "janus.plugin.textroom",
                    "session_id": sid,
                    "transaction": "t2",
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
                    "transaction": "t3",
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
            logging.info("DC open, start stream")

            @dc.on("open")
            def _(_):

                dc.send(
                    json.dumps(
                        {
                            "textroom": "join",
                            "transaction": "j",
                            "room": ROOM_ID,
                            "username": USERNAME,
                            "display": DISPLAY,
                            "datatype": "binary",
                        }
                    )
                )

                async def tx_loop():
                    global SEQ
                    while True:

                        chans = next_channels()
                        crsf = crsf_build_frame(
                            PacketsTypes.RC_CHANNELS_PACKED, {"channels": chans}
                        )

                        ts_ms = int(time.time() * 1000)
                        pkt = make_rtp(crsf, SEQ, ts_ms)
                        SEQ = (SEQ + 1) & 0xFFFF

                        logging.info("TX channels: %s", chans)
                        logging.info("TX raw: %s", pkt.hex(" "))
                        dc.send(pkt)

                        await asyncio.sleep(0.5)

                asyncio.create_task(tx_loop())

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
                    "transaction": "t4",
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
        logging.info("Drone streaming…")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(run())
