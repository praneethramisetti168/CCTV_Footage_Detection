# CHOICES.md — Three Key Engineering Decisions

## Decision 1: Detection Model Selection

### The Problem
The pipeline needs to detect and track individual people in retail CCTV footage at 1920×1080 resolution, handling groups, partial occlusion, and variable lighting — all while running on hardware that may not have a GPU.

### Options Considered

| Model | mAP | Speed (CPU) | Notes |
|-------|-----|-------------|-------|
| YOLOv8n | 37.3 | ~40ms/frame | Nano, lowest memory |
| YOLOv8s | 44.9 | ~85ms/frame | Small, better accuracy |
| YOLOv8m | 50.2 | ~180ms/frame | Medium, GPU recommended |
| RT-DETR-L | 53.0 | ~250ms/frame | Transformer-based, best accuracy |
| MediaPipe BlazePose | N/A | ~15ms/frame | Person detection only, no tracking |

### What AI Suggested
I asked Claude to compare YOLOv8 variants for retail foot traffic detection. It recommended **YOLOv8s** as the sweet spot — meaningfully better accuracy than nano (7.6 mAP points) at roughly 2× the latency. For a 15fps retail camera with frame-skipping, it argued, 85ms per frame is still real-time.

### What I Chose and Why
**YOLOv8n with frame-skip=3**, giving us an effective 10fps processing rate at ~40ms latency. My reasoning:

1. **CPU deployment**: The challenge says `docker compose up` must work on a clean machine. We cannot assume GPU. At 40ms/frame, YOLOv8n leaves headroom for tracking overhead. YOLOv8s at 85ms/frame would bottleneck on a standard laptop CPU.

2. **Accuracy is sufficient for counting**: The primary metric is entry/exit counts. Even YOLOv8n achieves >90% precision on unoccluded people. The edge cases (partial occlusion, groups) are handled by the tracking layer (ByteTrack) and confidence-aware reporting, not by raw detection accuracy.

3. **ByteTrack compensates**: Re-ID across frames means a brief detection miss doesn't create a new visitor — the track is maintained. This matters more for count accuracy than increasing model size.

4. **VLM was evaluated and rejected for zone classification**: I tested GPT-4V on 20 frames from CAM 1 for zone classification. Accuracy was ~85% but latency was 2.3s/frame — unusable for any pipeline. Rule-based rectangular zone definitions calibrated per camera are 100% deterministic, zero latency, and easy to update if the store rearranges products.

---

## Decision 2: Event Schema Design

### The Problem
The event schema must serve two masters: (1) the scoring harness that validates exact field names and types, and (2) the internal real-time pipeline that needs different event types for efficient processing.

### Options Considered

**Option A: Single unified schema for both pipeline and API**
- Pro: simpler codebase
- Con: challenge schema fields (visitor_id, is_staff, dwell_ms) pollute the internal pipeline that processes frames at 20fps

**Option B: Two schemas — internal StoreEvent + ChallengeEvent**
- Pro: internal pipeline optimised for speed; challenge schema used only at ingest boundary
- Con: translation layer needed

**Option C: Only challenge schema, everywhere**
- Pro: consistency
- Con: ChallengeEvent is heavyweight (visitor_id generation, session_seq tracking) per frame; too slow for FRAME_PROCESSED events

### What AI Suggested
Claude recommended Option B and suggested using Pydantic's discriminated unions to handle both schema types in the ingest endpoint. This was good advice — I implemented it as separate `StoreEvent` (internal) and `ChallengeEvent` (challenge-spec) classes.

### What I Chose and Why
**Option B**, with specific design choices:

- **`visitor_id` over raw `track_id`**: ByteTrack assigns monotonically incrementing integers that reset on restart and are camera-local. A `VIS_xxxxxx` token derived from (track_id, camera_id, entry_epoch) via SHA1 is globally unique per session, stable within a session, and conveys no raw track ID information (privacy-conscious). Importantly, when a REENTRY occurs, the original `visitor_id` is reused — which is the correct behaviour for conversion funnel accuracy.

- **`dwell_ms` not `dwell_s`**: Integer milliseconds avoid floating point precision issues in SQL aggregations and are unambiguous at sub-second granularity. 30 seconds of dwell = `30000` — no rounding needed.

- **`session_seq` in metadata, not top-level**: The challenge spec puts it in the `metadata` object. This allows the ingest endpoint to accept events from different pipeline implementations without breaking if `session_seq` is missing — it's nullable.

- **Challenge event types added to existing EventType enum**: The internal types (PERSON_ENTERED, ZONE_EXITED) are kept for backward compatibility with the demo mode and WebSocket dashboard. Challenge types (ENTRY, EXIT, ZONE_ENTER, etc.) are added as additional values. The ingest endpoint only accepts challenge types; the internal pipeline uses internal types.

---

## Decision 3: API Storage and Architecture

### The Problem
The API must persist events, compute real-time metrics, and serve concurrent requests. The storage choice determines deployment complexity, query flexibility, and cold-start time.

### Options Considered

**Storage options:**

| Option | Pros | Cons |
|--------|------|------|
| SQLite | Zero deps, file-based, WAL for concurrent reads | Single-writer, not horizontally scalable |
| PostgreSQL | Full ACID, concurrent writes, JSON operators | Requires separate container, cold-start complexity |
| TimescaleDB | Time-series optimised, auto-partitioning | Extension of PostgreSQL, higher setup cost |
| DuckDB | Columnar, fast analytics | Less mature for concurrent OLTP |

**Real-time aggregation options:**

| Option | Pros | Cons |
|--------|------|------|
| DB-only (query on every request) | Simple, always accurate | Slow for high-frequency metrics endpoints |
| In-memory only | Fast, zero DB reads | Lost on restart |
| Hybrid: in-memory + DB | Fast live + persistent history | Two sources of truth to sync |

### What AI Suggested
Claude strongly recommended PostgreSQL over SQLite, citing concurrent write limitations and the risk of database lock contention when the pipeline thread writes events at high frequency while API threads read. For the real-time layer, it suggested Redis with a sorted set for time-series data.

### What I Chose and Why

**SQLite with WAL mode + in-memory StoreAggregator hybrid**, for these reasons:

1. **Acceptance gate requirement**: `docker compose up` must start everything. PostgreSQL requires a second service, health checks, and initialization time. SQLite starts instantly with zero configuration, satisfying the gate reliably.

2. **Write frequency analysis**: The pipeline processes frames at ~10fps with frame-skip=3. Only non-FRAME_PROCESSED events hit the database — roughly 1 write per second peak. SQLite with WAL mode handles this comfortably; write lock contention only occurs when multiple threads write simultaneously, which our single pipeline thread prevents.

3. **Hybrid metrics**: The `StoreAggregator` maintains live in-memory counts (zone occupancy, current footfall) updated on every event. The DB is queried for aggregate metrics (avg dwell, funnel counts) that don't need frame-level granularity. This gives sub-millisecond response times on the hot metrics while preserving history across restarts.

4. **On Redis**: I disagreed with the AI on Redis. Adding a Redis container means another service to health-check, another failure mode, and another dependency to install. The asyncio.Queue event bus provides the same pub/sub functionality within the process with zero network overhead.

5. **Migration path**: `DATABASE_URL=postgresql://...` in the `.env` file switches storage without code changes — SQLAlchemy's ORM layer abstracts the difference. This is documented in the README.
