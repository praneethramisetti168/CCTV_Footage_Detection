# DESIGN.md — Store Intelligence System Architecture

## Overview

This system turns raw CCTV footage from a Purplle beauty retail store (Brigade Road, Bangalore) into a live analytics API. Five camera angles — entry/exit, two floor views, restricted storage, and billing counter — feed a detection pipeline that emits structured events, which are ingested into a REST API serving real-time store intelligence.

The north star metric driving every design decision is **offline store conversion rate**: the fraction of unique visitors who complete a purchase. Every pipeline stage either improves the accuracy of this number (detection layer) or makes it actionable (API layer).

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  CCTV CLIPS  (5 cameras × ~2.3 min @ 1080p)                      │
│  CAM3:Entry  CAM1:Floor  CAM2:Floor  CAM4:Storage  CAM5:Billing  │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  DETECTION PIPELINE  (pipeline/detect.py)                         │
│  YOLOv8n person detection + ByteTrack multi-object tracking       │
│  ├── Staff detection (camera role + dark uniform heuristic)       │
│  ├── Direction detection (entry/exit line crossing at CAM3)       │
│  ├── Re-entry tracking (spatial proximity + 5-min window)         │
│  ├── Zone tracking (per-camera zone definitions)                  │
│  └── SessionTracker → VIS_xxxxxx visitor tokens                   │
└──────────────┬───────────────────────────────────────────────────┘
               │  ChallengeEvents → JSONL / POST /events/ingest
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  INTELLIGENCE API  (FastAPI + SQLite)                              │
│  ├── POST /events/ingest  (idempotent, batch 500, partial success)│
│  ├── GET  /stores/{id}/metrics  (real-time, POS-correlated)       │
│  ├── GET  /stores/{id}/funnel   (session-based, re-entry safe)    │
│  ├── GET  /stores/{id}/heatmap  (normalised 0-100, confidence)    │
│  ├── GET  /stores/{id}/anomalies (severity + suggested_action)    │
│  └── GET  /health               (STALE_FEED detection)            │
└──────────────┬───────────────────────────────────────────────────┘
               │  asyncio.Queue event bus (internal pipeline)
               ├── In-memory StoreAggregator (live snapshot)
               ├── SQLite via SQLAlchemy ORM (persistence)
               ├── Anomaly Detector (rule-based + Z-score)
               └── WebSocket broadcast → Live Dashboard
┌──────────────────────────────────────────────────────────────────┐
│  LIVE DASHBOARD  (HTML + Chart.js + WebSocket)                    │
│  Real-time visitor count, zone heatmap, anomaly alerts            │
└──────────────────────────────────────────────────────────────────┘
```

---

## Component Deep Dives

### Detection Pipeline

**Model**: YOLOv8n (Ultralytics) with ByteTrack multi-object tracking. Selected for its balance of speed and accuracy on 1080p retail footage at 15-30 fps. The nano variant processes frames at ~40ms each on CPU, sufficient for our 3-frame-skip strategy (every 3rd frame processed).

**Camera role mapping**: Each of the 5 cameras has a designated role that determines which events it can emit:
- `CAM_ENTRY_01` (CAM 3): Entry/exit threshold detection using vertical centroid movement across a configurable threshold line. Downward crossing = ENTRY, upward = EXIT.
- `CAM_FLOOR_01/02` (CAM 1, 2): Zone tracking across four product zones (SKINCARE, MAKEUP, HAIRCARE, FRAGRANCE). ZONE_DWELL emitted every 30s of continuous presence.
- `CAM_STORAGE_01` (CAM 4): All detections flagged `is_staff=true`. No customer events possible from this camera.
- `CAM_BILLING_01` (CAM 5): BILLING_QUEUE_JOIN when queue depth >1. BILLING_QUEUE_ABANDON emitted on track loss without POS correlation.

**Staff detection**: Three-layer heuristic:
1. Camera role: storage camera → always staff
2. Position: billing camera, left-side position (behind counter) → cashier
3. Uniform: >55% dark pixel ratio in torso bounding box region → dark uniform

**Re-entry detection**: SessionTracker maintains an exited-session pool. When a new track appears within 5 minutes of an EXIT at similar spatial coordinates (within 150px Euclidean distance), a REENTRY event is emitted and the original visitor_id is reused rather than creating a new session.

**Session tokens**: VIS_xxxxxx generated as SHA1 hash of (track_id, camera_id, entry_epoch), truncated to 6 hex chars. Stable within a session, distinct across sessions.

### Intelligence API

**Ingest endpoint**: `POST /events/ingest` validates each event against the `ChallengeEvent` Pydantic model. Idempotency is enforced by pre-fetching all `event_id` values in the incoming batch from the database before insertion. Malformed events are collected into an `errors` list and returned in the response without failing the entire batch.

**Metrics computation**: Real-time metrics combine two data sources — the SQLite database (persistent, query-able) and the in-memory `StoreAggregator` (live frame-level data). This hybrid approach avoids cache staleness while remaining fast.

**POS Correlation**: The `POSCorrelator` loads `pos_transactions.csv` on startup. Conversion rate is computed by matching visitor billing zone entry times to POS transaction timestamps within a 5-minute window. This matches the challenge specification exactly.

**Anomaly detection**: Two strategies run concurrently:
- Rule-based: capacity exceeded, restricted zone dwell, after-hours presence
- Statistical: Z-score on rolling 50-sample window per zone detects unusual surges even when absolute counts are within limits

### Storage

SQLite with WAL journal mode via SQLAlchemy. Chosen for zero external dependencies — the system runs with `docker compose up` from a cold start without any separate database container. The `DATABASE_URL` environment variable makes this swappable to PostgreSQL without code changes.

### Live Dashboard

WebSocket-connected HTML/JS dashboard served from the same FastAPI process. Receives real-time frames, anomaly alerts, and analytics snapshots. Chart.js renders the footfall trend; a custom SVG zone map shows heatmap coloring. This satisfies Part E (bonus) by showing live metrics updating as pipeline events flow in.

---

## AI-Assisted Decisions

### 1. Event Bus Architecture: asyncio.Queue vs Kafka

When designing the internal event routing from the pipeline thread to the async API, I asked Claude to evaluate `asyncio.Queue` vs Apache Kafka vs Redis Streams. The AI recommended Kafka for production scale, citing its replay capability and consumer group support as significant advantages.

**I disagreed and chose `asyncio.Queue`**. The rationale: the challenge runs on a single machine with `docker compose up`. Kafka would require a separate container, a ZooKeeper dependency (or KRaft mode configuration), and adds ~10 seconds to startup time. For a system that needs to demonstrate end-to-end functionality in a challenge context, simplicity wins. The `event_bus.py` abstraction means Kafka can be dropped in later without changing any consumer code.

### 2. VLM for Zone Classification

I prompted GPT-4V with a frame from CAM 1 and asked it to classify which product zone a customer was standing in. The result was accurate (~85% agreement with ground truth on 20 test frames), but each inference took 2.3 seconds. With 1920×1080 frames at 30fps, this would process approximately 1 frame per 70 frames — far too slow for even batch processing.

**I overrode the AI suggestion** and implemented rule-based zone classification using camera-specific rectangular regions calibrated against each clip. This runs in microseconds. The VLM approach would be valuable in a production deployment with a dedicated GPU inference server; I documented this trade-off in CHOICES.md.

### 3. Staff Detection Approach

The AI suggested training a fine-tuned classifier on staff uniforms for robust staff detection. I partially agreed: I implemented the dark-uniform heuristic (which the AI helped calibrate to a 55% dark-pixel threshold after testing on CAM 4 frames) but did not train a custom model. The camera-role heuristic (storage = always staff) covers the most important case without any ML overhead, and the dark uniform heuristic adds coverage for the billing camera cashier.
