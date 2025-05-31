#!/usr/bin/env python3
import asyncio
import json
import uuid
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
import time

try:
    from aiortc.exceptions import MediaStreamError  
except ImportError:  
    try:
        from aiortc import MediaStreamError 
    except ImportError:

        class MediaStreamError(Exception):
            """Fallback if MediaStreamError is missing from aiortc"""

            pass


import pygame
import cv2
import os

JANUS_WS = "ws://127.0.0.1:8188"
STREAM_ID = 1001  
KEEPALIVE_INTERVAL = 30  


async def run_pult(video_filename: str) -> None:
    """Connect to Janus and receive video with latency measurement."""

    stream_start = None
    first_frame = True

    async with websockets.connect(JANUS_WS, subprotocols=["janus-protocol"]) as ws:
        print("[PULT] Connected to Janus WebSocket")


        transaction = str(uuid.uuid4())
        await ws.send(json.dumps({"janus": "create", "transaction": transaction}))
        reply = json.loads(await ws.recv())
        if reply.get("janus") != "success" or reply.get("transaction") != transaction:
            raise RuntimeError(f"Failed to create session: {reply}")
        session_id = reply["data"]["id"]

        print(f"[PULT] Session created: {session_id}")


        transaction = str(uuid.uuid4())
        await ws.send(
            json.dumps(
                {
                    "janus": "attach",
                    "session_id": session_id,
                    "plugin": "janus.plugin.streaming",
                    "transaction": transaction,
                }
            )
        )
        reply = json.loads(await ws.recv())
        if reply.get("janus") != "success" or reply.get("transaction") != transaction:
            raise RuntimeError(f"Failed to attach to plugin: {reply}")
        handle_id = reply["data"]["id"]

        print(f"[PULT] Streaming plugin attached: {handle_id}")

        transaction = str(uuid.uuid4())

        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "session_id": session_id,
                    "handle_id": handle_id,
                    "body": {"request": "watch", "id": STREAM_ID},
                    "transaction": transaction,
                }
            )
        )

        jsep_offer = None

        while jsep_offer is None:
            msg = json.loads(await ws.recv())
            if msg.get("sender") == handle_id and "jsep" in msg:
                jsep_offer = msg["jsep"]

            elif msg.get("janus") == "keepalive":
                continue
            else:
                print(f"[PULT] ↳ Other message: {msg}")
        print("[PULT] Received JSEP offer from server")

        pc = RTCPeerConnection()
        pygame.init()
        screen = None
        video_writer = None

        @pc.on("track")
        def on_track(track):
            nonlocal screen, video_writer, first_frame, stream_start

            if track.kind != "video":
                return

            async def recv_video():
                nonlocal screen, video_writer, first_frame, stream_start

                while True:
                    try:
                        frame = await track.recv()
                    except MediaStreamError:
                        break

                    pts_seconds = frame.pts * frame.time_base

                    if first_frame:
                        stream_start = time.time() - pts_seconds
                        first_frame = False

                        img = frame.to_ndarray(format="bgr24")
                        surf = pygame.surfarray.make_surface(
                            cv2.cvtColor(img, cv2.COLOR_BGR2RGB).swapaxes(0, 1)
                        )
                        screen = pygame.display.set_mode(surf.get_size())
                        pygame.display.set_caption("Drone Video")
                        h, w, _ = img.shape

                        video_writer = cv2.VideoWriter(
                            video_filename,
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            15,
                            (w, h),
                        )
                        print(f"[PULT] Recording video to file: {video_filename}")

                    recv_time = time.time()
                    latency_ms = (recv_time - (stream_start + pts_seconds)) * 1000
                    print(f"[PULT] Video latency: {latency_ms:.2f} ms")

                    img = frame.to_ndarray(format="bgr24")

                    surf = pygame.surfarray.make_surface(
                        cv2.cvtColor(img, cv2.COLOR_BGR2RGB).swapaxes(0, 1)
                    )
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            pygame.quit()
                            return

                    screen.blit(surf, (0, 0))
                    pygame.display.flip()

                    video_writer.write(img)

            asyncio.create_task(recv_video())

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=jsep_offer["sdp"], type=jsep_offer["type"])
        )
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        transaction = str(uuid.uuid4())

        await ws.send(
            json.dumps(
                {
                    "janus": "message",
                    "session_id": session_id,
                    "handle_id": handle_id,
                    "body": {"request": "start"},
                    "jsep": {"type": answer.type, "sdp": answer.sdp},
                    "transaction": transaction,
                }
            )
        )

        async def keepalive():
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                await ws.send(
                    json.dumps({"janus": "keepalive", "session_id": session_id})
                )

        asyncio.create_task(keepalive())

        try:
            await asyncio.Future()
        finally:
            await pc.close()
            if video_writer:
                video_writer.release()
            pygame.quit()
            print("[PULT] Terminated")


if __name__ == "__main__":
    try:
        filename = input("Enter filename to save video (e.g., drone1.mp4): ").strip()
        if not filename:
            filename = "drone_record.mp4"
        if not os.path.splitext(filename)[1]:
            filename += ".mp4"

        asyncio.run(run_pult(filename))
    except KeyboardInterrupt:
        print("\n[PULT] Stopped by user")
