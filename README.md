# CCTV_Footage_Detection

Complete, practical store-intelligence project for detecting people, events, and anomalies from CCTV streams and correlating those events with POS data. Intended as a demonstration/benchmark solution: it contains a detection+tracking pipeline, analytics, a WebSocket API, and a simple dashboard for visualization.

---

## Contents (short)

- `backend/` — API, database helpers, analytics, and model files.
- `pipeline/` — detector, tracker, emitter, and video-processing utilities.
- `dashboard/` — static frontend to visualize events (HTML/JS/CSS).
- `sample_data/` — small thumbnails and a demo video generator script.
- `events_output/` — example JSONL event logs.
- `yolov8n.pt` — small YOLOv8 weights used for demos.

---

## Requirements

- Python 3.10+ (Windows or Unix)
- Git
- Optional: Docker & Docker Compose for containerized runs

If you plan to run detection locally you will need `torch`/`ultralytics` installed — see `backend/requirements.txt` for the exact Python packages.

---

## Setup (local, Windows and Unix)

Windows (PowerShell):

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

Unix/macOS (bash):

```bash
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

---

## Quick Start — Run everything (recommended flows)

1) Run backend API (development):

```bash
cd backend
python main.py
# By default the API listens on http://127.0.0.1:8000 (check console output)
```

2) Run the pipeline against the demo clip or your own video:

```bash
# Generate a short demo video (optional)
python sample_data/generate_demo_video.py

# Run detection pipeline (example)
python pipeline/detect.py
```

3) Dashboard: open `dashboard/index.html` in the browser or serve it with a static server. Example using Python HTTP server (recommended when using API/WebSocket locally):

```bash
cd dashboard
python -m http.server 8000
# Open http://127.0.0.1:8000 in your browser
```

4) Convenience start script (Windows):

```powershell
.\start.bat    # runs pipeline + backend in the configured way (Windows)
```

If you are on Unix and a `start.sh` is available, use `./start.sh` instead.

---

## Docker / Docker Compose

To run the project in Docker (if you prefer containerization):

```bash
docker-compose up --build
```

This uses the `Dockerfile` and `docker-compose.yml` in the repo root. Adjust volumes for large video files outside the repo.

---

## Dashboard details

- The dashboard is a static frontend in `dashboard/` that connects to the local backend WebSocket to receive live events. If you serve the dashboard via `python -m http.server` or another static server, point the dashboard configuration (if needed) to the backend API/WebSocket URL.
- To view saved example events, load files from `events_output/` using the dashboard's sample loader (if provided).

---

## Data & model notes

- `yolov8n.pt` is included for demo purposes only (small weights). For production use, replace with a more suitable model and keep weights out of the repo if they are large.
- Full CCTV raw clips were removed from the public history to keep the repo small — store video data externally and update local config paths.

---

## Tests

Run unit tests with pytest from repo root:

```bash
pytest -q
```

There are tests under `tests/` covering pipeline and analytics components.

---

## Troubleshooting & tips

- If `python main.py` fails due to missing packages, ensure the virtual environment is activated and `pip install -r backend/requirements.txt` completed successfully.
- If the dashboard cannot connect to WebSocket, check the backend console for the WebSocket bind address and ensure CORS / firewall rules allow it.
- Large files: add them to an external storage location and update local config paths; do not commit large video files to the repository.

---

## Contributing

Fork, add feature branches, and open pull requests. Add tests for non-trivial changes and keep public secrets out of the repo.

---

If you want, I can further tailor the `start.bat` content and add an explicit `start.sh` equivalent for Unix. Tell me which commands you prefer to run with `start.bat` and I will document them verbatim.

## Key Components

- **Backend**: FastAPI-based API and WebSocket endpoints under `backend/`.
- **Pipeline**: Video processing, object detection (YOLOv8), tracking, and event emitters in `pipeline/` and `backend/pipeline/`.
- **Analytics**: Aggregation and anomaly detection logic in `backend/analytics/`.
- **Dashboard**: Simple browser dashboard in `dashboard/` to visualize events.
- **Sample data**: Small thumbnails and demo-generation script in `sample_data/`.

## Quickstart (local)

Prerequisites: Python 3.10+ and Git.

1. Create a virtual environment and install requirements:

```bash
python -m venv venv
venv\Scripts\activate    # Windows
pip install -r backend/requirements.txt
```

2. Run the backend API (development):

```bash
cd backend
python main.py
```

3. Run the pipeline against a demo video (or your own placed in `sample_data/`):

```bash
python sample_data/generate_demo_video.py   # generates a short demo clip
python pipeline/detect.py                   # or use provided scripts
```

4. Open the dashboard at `dashboard/index.html` in a browser to view events.

## Data & Model Notes

- The repository includes `yolov8n.pt` (small model weights) for detection.
- Raw full-length CCTV clips were intentionally removed from repository history to keep the repo small; add large video files to a separate storage location and reference them in `sample_data/` or `data/` locally.

## Running Tests

From the repo root:

```bash
pytest -q
```

## Project Structure (high level)

- `backend/` — API, database, analytics, model files
- `pipeline/` — detector/tracker/emitter scripts
- `dashboard/` — static dashboard frontend
- `sample_data/` — demo-generation scripts and small thumbnails
- `events_output/` — example event logs (JSONL)

## Contributing

If you'd like to contribute, open an issue or submit a pull request. Follow the existing code style and add tests for significant changes.

## License & Credits

This repository was prepared as a submission for a technical challenge. Check project files for any included third-party licenses. Credit: Praneeth Ramisetti and contributors.
