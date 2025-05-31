import asyncio
import aiohttp
import subprocess
import uuid
import logging

JANUS_URL = "http://127.0.0.1:8088/janus"
JANUS_HOST = "127.0.0.1"
VIDEO_PORT = 8004
DATA_PORT = 8006
STREAM_ID = 1001

logging.basicConfig(filename="drone_video.log", level=logging.INFO)


async def main():
    async with aiohttp.ClientSession() as session:
        base = JANUS_URL
        try:
            r = await session.post(
                base, json={"janus": "create", "transaction": str(uuid.uuid4())}
            )
            data = await r.json()
            session_id = data["data"]["id"]
            base = f"{base}/{session_id}"
        except Exception as e:
            logging.error(f"[Session creation error] {e}")
            return

        try:
            r = await session.post(
                base,
                json={
                    "janus": "attach",
                    "plugin": "janus.plugin.streaming",
                    "transaction": str(uuid.uuid4()),
                },
            )
            data = await r.json()
            handle_id = data["data"]["id"]
            handle_url = f"{base}/{handle_id}"
        except Exception as e:
            logging.error(f"[Error attaching to streaming plugin] {e}")
            return

        mountpoint_config = {
            "request": "create",
            "type": "rtp",
            "id": STREAM_ID,
            "description": "Drone video stream",
            "video": True,
            "videoport": VIDEO_PORT,
            "videopt": 96,
            "videortpmap": "H264/90000",
            "data": True,
            "dataport": DATA_PORT,
            "datatype": "binary",
        }

        try:
            r = await session.post(
                handle_url,
                json={
                    "janus": "message",
                    "body": mountpoint_config,
                    "transaction": str(uuid.uuid4()),
                },
            )
            data = await r.json()
            if data.get("janus") == "success":
                print(f"Mountpoint {STREAM_ID} created successfully")
            else:
                print("Failed to create mountpoint:", data)
                return
        except Exception as e:
            logging.error(f"[Mountpoint creation error] {e}")
            return

        gst = [
            "gst-launch-1.0",
            "v4l2src",
            "device=/dev/video0",
            "!",
            "videoconvert",
            "!",
            "x264enc",
            "tune=zerolatency",
            "speed-preset=ultrafast",
            "!",
            "rtph264pay",
            "config-interval=1",
            "pt=96",
            "!",
            "udpsink",
            f"host={JANUS_HOST}",
            f"port={VIDEO_PORT}",
        ]

        try:
            gst_process = subprocess.Popen(gst)
            print("Video stream started")
        except Exception as e:
            logging.error(f"[GStreamer launch error] {e}")
            return

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            gst_process.terminate()
            await asyncio.to_thread(gst_process.wait)


if __name__ == "__main__":
    asyncio.run(main())
