#!/usr/bin/env bash
# run.sh — Process all 5 CCTV clips and feed events into the Intelligence API
#
# Usage:
#   bash pipeline/run.sh                          # JSONL output only
#   bash pipeline/run.sh --api http://localhost:8000  # + live API ingest
#
# Output: events_output/events_<CAMERA_ID>.jsonl per camera

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CLIPS_DIR="$ROOT_DIR/CCTV Footage-20260529T160731Z-3-00144614ea (1)/CCTV Footage"
OUTPUT_DIR="$ROOT_DIR/events_output"
DETECT_SCRIPT="$ROOT_DIR/backend/pipeline/detect.py"
API_ARG=""

# Parse optional --api argument
if [ "$1" = "--api" ] && [ -n "$2" ]; then
    API_ARG="--api $2"
    echo "Live API ingest enabled: $2"
fi

mkdir -p "$OUTPUT_DIR"

echo "======================================================"
echo "  Store Intelligence — CCTV Detection Pipeline"
echo "  Store: STORE_BLR_001 | Brigade Road, Bangalore"
echo "======================================================"
echo ""

# Activate venv if present
if [ -f "$ROOT_DIR/backend/venv/bin/activate" ]; then
    source "$ROOT_DIR/backend/venv/bin/activate"
fi

run_clip() {
    local clip="$1"
    local camera="$2"
    local role="$3"
    echo "▶ Processing: $camera ($role)"
    echo "  Clip: $clip"
    python "$DETECT_SCRIPT" \
        --clip "$clip" \
        --camera "$camera" \
        --role "$role" \
        --output "$OUTPUT_DIR/events.jsonl" \
        --skip 3 \
        --conf 0.35 \
        $API_ARG
    echo "  ✓ Done"
    echo ""
}

# CAM 3 = Entry/Exit threshold
run_clip "$CLIPS_DIR/CAM 3.mp4" "CAM_ENTRY_01" "entry"

# CAM 1 = Main floor (skincare/makeup)
run_clip "$CLIPS_DIR/CAM 1.mp4" "CAM_FLOOR_01" "floor"

# CAM 2 = Wider floor angle
run_clip "$CLIPS_DIR/CAM 2.mp4" "CAM_FLOOR_02" "floor"

# CAM 4 = Storage / restricted (all is_staff=True)
run_clip "$CLIPS_DIR/CAM 4.mp4" "CAM_STORAGE_01" "storage"

# CAM 5 = Billing counter + POS area
run_clip "$CLIPS_DIR/CAM 5.mp4" "CAM_BILLING_01" "billing"

echo "======================================================"
echo "  All clips processed!"
echo "  Events written to: $OUTPUT_DIR/"
echo ""

# Combine all JSONL files
cat "$OUTPUT_DIR"/events_CAM_*.jsonl > "$OUTPUT_DIR/all_events.jsonl" 2>/dev/null || true
TOTAL=$(wc -l < "$OUTPUT_DIR/all_events.jsonl" 2>/dev/null || echo 0)
echo "  Total events: $TOTAL"

# If API is available and we haven't already ingested, do a bulk ingest
if [ -n "$API_ARG" ]; then
    # Extract the URL value from the $API_ARG string ("--api http://...") and pass it to Python
    API_URL="$2"
    echo "  Ingesting combined events into API ($API_URL)..."
    python - "$API_URL" "$OUTPUT_DIR" <<'PYEOF'
import json, sys, os
try:
    import httpx
except ImportError:
    import urllib.request, urllib.error
    httpx = None

api_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
output_dir = sys.argv[2] if len(sys.argv) > 2 else "events_output"
jsonl_path = os.path.join(output_dir, "all_events.jsonl")

if not os.path.isfile(jsonl_path):
    print(f"  No combined events file found at {jsonl_path}")
    sys.exit(0)

events = []
with open(jsonl_path) as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass

batch_size = 500
accepted = rejected = 0
for i in range(0, len(events), batch_size):
    batch = events[i:i+batch_size]
    payload = json.dumps({"events": batch}).encode()
    try:
        if httpx:
            resp = httpx.post(f"{api_url}/events/ingest", json={"events": batch}, timeout=30)
            if resp.status_code == 200:
                d = resp.json()
                accepted += d.get("accepted", 0)
                rejected += d.get("rejected", 0)
        else:
            req = urllib.request.Request(
                f"{api_url}/events/ingest",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read())
                accepted += d.get("accepted", 0)
                rejected += d.get("rejected", 0)
    except Exception as e:
        print(f"  Batch {i//batch_size+1} failed: {e}")

print(f"  Ingest complete: {accepted} accepted, {rejected} rejected")
PYEOF
fi

echo "======================================================"
echo "  Done. Check events_output/ for JSONL event files."
echo "  Run: curl http://localhost:8000/stores/STORE_BLR_001/metrics"
echo "======================================================"
