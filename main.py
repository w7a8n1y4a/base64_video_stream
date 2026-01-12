import time
import sys
import os
from pepeunit_client.client import PepeunitClient

DEFAULT_FPS = 10


def main():
    if len(sys.argv) < 2:
        print("Usage: python stream.py <frames_file.txt> [-fps N]")
        sys.exit(1)

    frames_file = sys.argv[1]

    fps = DEFAULT_FPS
    if "-fps" in sys.argv:
        i = sys.argv.index("-fps")
        fps = float(sys.argv[i + 1])
        if fps <= 0:
            raise ValueError("FPS must be > 0")

    if not os.path.exists(frames_file):
        raise FileNotFoundError(frames_file)

    delay = 1.0 / fps

    client = PepeunitClient(
        env_file_path="env.json",
        schema_file_path="schema.json",
        log_file_path="log.json",
        enable_mqtt=True,
        enable_rest=True,
    )

    client.mqtt_client.connect()

    with open(frames_file, "r", encoding="utf-8") as f:
        frames = [line.strip() for line in f if line.strip()]

    print(f"Loaded {len(frames)} frames")
    print(f"Streaming at {fps} FPS")

    try:
        while True:
            for i, frame_b64 in enumerate(frames):
                client.publish_to_topics("full_frame_stream/pepeunit", frame_b64)
                print(f"Sent frame {i + 1}/{len(frames)}")
                time.sleep(delay)

    except KeyboardInterrupt:
        print("\nStopped by user")

    finally:
        client.mqtt_client.disconnect()


if __name__ == "__main__":
    main()

