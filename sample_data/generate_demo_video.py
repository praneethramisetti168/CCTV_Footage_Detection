"""
Generate a synthetic demo video for testing the CCTV pipeline.

Creates a 640x480 MP4 with animated colored blobs simulating people
moving around a store. Run once to produce 'demo_video.mp4'.

Usage:
    cd sample_data
    python generate_demo_video.py

Then in .env set: VIDEO_SOURCE=sample_data/demo_video.mp4
"""
import cv2
import numpy as np
import math
import random
import os

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "demo_video.mp4")
WIDTH, HEIGHT = 640, 480
FPS = 20
DURATION_SECONDS = 60
TOTAL_FRAMES = FPS * DURATION_SECONDS

COLORS = [
    (57, 255, 20),    # green
    (255, 165, 0),    # orange
    (30, 144, 255),   # blue
    (255, 0, 200),    # pink
    (0, 200, 255),    # cyan
    (255, 255, 0),    # yellow
    (200, 0, 255),    # purple
]

ZONES = [
    {"name": "Entrance",   "x1": 0,   "y1": 0,   "x2": 213, "y2": 240, "color": (57, 255, 20)},
    {"name": "Checkout",   "x1": 213, "y1": 0,   "x2": 427, "y2": 240, "color": (255, 165, 0)},
    {"name": "Aisle A",    "x1": 0,   "y1": 240, "x2": 213, "y2": 480, "color": (30, 144, 255)},
    {"name": "Aisle B",    "x1": 213, "y1": 240, "x2": 427, "y2": 480, "color": (255, 0, 200)},
    {"name": "Storage",    "x1": 427, "y1": 0,   "x2": 640, "y2": 480, "color": (220, 0, 0)},
]

TARGETS = [(106, 120), (320, 120), (106, 360), (320, 360), (533, 240)]


class SimPerson:
    def __init__(self, pid):
        self.pid = pid
        self.x = float(random.randint(30, 600))
        self.y = float(random.randint(30, 450))
        self.tx, self.ty = random.choice(TARGETS)
        self.speed = random.uniform(1.8, 3.5)
        self.color = COLORS[pid % len(COLORS)]
        self.w = random.randint(32, 46)
        self.h = random.randint(70, 100)

    def step(self):
        dx, dy = self.tx - self.x, self.ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 15:
            self.tx, self.ty = random.choice(TARGETS)
        else:
            self.x += (dx / dist) * self.speed
            self.y += (dy / dist) * self.speed


def draw_frame(persons, frame_idx):
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:] = (20, 26, 40)

    # Draw zones
    for z in ZONES:
        overlay = frame.copy()
        cv2.rectangle(overlay, (z["x1"], z["y1"]), (z["x2"], z["y2"]), z["color"], -1)
        cv2.addWeighted(overlay, 0.08, frame, 0.92, 0, frame)
        cv2.rectangle(frame, (z["x1"], z["y1"]), (z["x2"], z["y2"]), z["color"], 1)
        cv2.putText(frame, z["name"], (z["x1"] + 5, z["y1"] + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, z["color"], 1)

    # Draw persons as filled rects (simulating detection boxes)
    for p in persons:
        x1 = int(p.x - p.w / 2)
        y1 = int(p.y - p.h / 2)
        x2 = int(p.x + p.w / 2)
        y2 = int(p.y + p.h / 2)
        cv2.rectangle(frame, (x1, y1), (x2, y2), p.color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 255), 1)
        cv2.circle(frame, (int(p.x), int(p.y)), 3, (255, 255, 255), -1)
        label = f"P{p.pid}"
        cv2.putText(frame, label, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38, p.color, 1)

    # HUD
    ts = f"Frame {frame_idx}/{TOTAL_FRAMES}  |  People: {len(persons)}"
    cv2.putText(frame, ts, (8, HEIGHT - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 150, 150), 1)
    return frame


def generate():
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(OUTPUT_PATH, fourcc, FPS, (WIDTH, HEIGHT))

    persons = [SimPerson(i) for i in range(4)]
    next_id = 4
    spawn_cooldown = 0

    print(f"Generating {TOTAL_FRAMES} frames → {OUTPUT_PATH}")
    for fi in range(TOTAL_FRAMES):
        # Spawn/remove logic
        spawn_cooldown -= 1
        if spawn_cooldown <= 0 and len(persons) < 9 and random.random() < 0.04:
            persons.append(SimPerson(next_id))
            next_id += 1
            spawn_cooldown = FPS * 5

        if len(persons) > 3 and random.random() < 0.005:
            persons.pop(random.randrange(len(persons)))

        for p in persons:
            p.step()

        frame = draw_frame(persons, fi + 1)
        writer.write(frame)

        if (fi + 1) % 200 == 0:
            print(f"  {fi + 1}/{TOTAL_FRAMES} frames written…")

    writer.release()
    print(f"\n✅ Demo video saved: {OUTPUT_PATH}")
    print("   Set VIDEO_SOURCE=sample_data/demo_video.mp4 in .env to use it.")


if __name__ == "__main__":
    generate()
