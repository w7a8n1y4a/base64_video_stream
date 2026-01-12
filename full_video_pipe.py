import cv2
import numpy as np
import base64
import os
import sys
from tqdm import tqdm
from multiprocessing import Pool, cpu_count

WIDTH = 128
HEIGHT = 64
BYTES_PER_FRAME = WIDTH * HEIGHT // 8
BATCH_SIZE = 32  # обработка батчами для памяти

def floyd_steinberg_dither(img):
    img = img.astype(np.float32)
    h, w = img.shape

    for y in range(h - 1):
        for x in range(1, w - 1):
            old = img[y, x]
            new = 255 if old >= 128 else 0
            err = old - new
            img[y, x] = new

            img[y, x + 1]     += err * 7 / 16
            img[y + 1, x - 1] += err * 3 / 16
            img[y + 1, x]     += err * 5 / 16
            img[y + 1, x + 1] += err * 1 / 16

    return np.clip(img, 0, 255).astype(np.uint8)

def enhance_for_binary(gray):
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast = clahe.apply(gray)

    blur1 = cv2.GaussianBlur(contrast, (0, 0), 1.0)
    blur2 = cv2.GaussianBlur(contrast, (0, 0), 2.0)
    dog = cv2.subtract(blur1, blur2)

    return cv2.addWeighted(contrast, 1.0, dog, 1.5, 0)

def frame_to_sh1106_buffer(frame_bin):
    buffer = bytearray(BYTES_PER_FRAME)
    for y in range(HEIGHT):
        for x in range(WIDTH):
            if frame_bin[y, x]:
                page = y // 8
                bit_pos = y % 8
                index = page * WIDTH + x
                buffer[index] |= (1 << bit_pos)
    return bytes(buffer)

def process_frame(frame):
    frame = cv2.resize(frame, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = enhance_for_binary(gray)
    binary = floyd_steinberg_dither(enhanced)
    binary = np.where(binary >= 128, 255, 0).astype(np.uint8)
    binary[0, :] = 0
    binary[-1, :] = 0
    binary[:, 0] = 0
    binary[:, -1] = 0
    buf = frame_to_sh1106_buffer(binary)
    return base64.b64encode(buf).decode("ascii")


def main():
    if len(sys.argv) < 3:
        print("Usage: python full_video_pipe_multiproc.py <input.mp4> <output.txt> [-fps N] [-cpu M]")
        sys.exit(1)

    video_file = sys.argv[1]
    output_file = sys.argv[2]

    target_fps = None
    if "-fps" in sys.argv:
        i = sys.argv.index("-fps")
        target_fps = float(sys.argv[i + 1])
        if target_fps <= 0:
            raise ValueError("FPS must be > 0")

    num_cpu = cpu_count()
    if "-cpu" in sys.argv:
        i = sys.argv.index("-cpu")
        num_cpu = int(sys.argv[i + 1])
        if num_cpu <= 0 or num_cpu > cpu_count():
            raise ValueError(f"CPU count must be 1..{cpu_count()}")

    if not os.path.exists(video_file):
        raise FileNotFoundError(video_file)

    cap = cv2.VideoCapture(video_file)
    if not cap.isOpened():
        raise RuntimeError("Cannot open video")

    src_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if src_fps <= 0:
        src_fps = 30.0

    if target_fps is None:
        target_fps = src_fps

    frame_interval = src_fps / target_fps

    pool = Pool(processes=num_cpu)

    with open(output_file, "w", encoding="utf-8") as out, \
         tqdm(total=total_frames, unit="frame", desc="Processing") as pbar:

        frame_index = 0
        next_frame = 0.0
        batch = []

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_index >= next_frame:
                batch.append(frame)
                next_frame += frame_interval

            frame_index += 1
            pbar.update(1)

            if len(batch) >= BATCH_SIZE:
                results = pool.map(process_frame, batch)
                for r in results:
                    out.write(r + "\n")
                batch.clear()

        if batch:
            results = pool.map(process_frame, batch)
            for r in results:
                out.write(r + "\n")
            batch.clear()

    cap.release()
    pool.close()
    pool.join()
    print("Done.")

if __name__ == "__main__":
    main()

