from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # App
    app_name: str = "Store Intelligence System"
    app_version: str = "1.0.0"
    debug: bool = False

    # Store identity
    store_id: str = "STORE_BLR_001"
    store_name: str = "Brigade_Bangalore"

    # Database
    database_url: str = "sqlite:///./store_intelligence.db"

    # File paths (overridden by env vars in Docker)
    clips_dir: str = "./CCTV Footage-20260529T160731Z-3-00144614ea (1)/CCTV Footage"
    events_output_dir: str = "./events_output"
    pos_csv_path: str = "./pos_transactions.csv"
    store_layout_json: str = "./store_layout.json"

    # Video source: integer (webcam index), file path, RTSP URL, or "demo"
    video_source: str = "demo"

    # YOLO
    yolo_model: str = "yolov8n.pt"
    yolo_conf_threshold: float = 0.40
    frame_skip: int = 3  # Process every Nth frame

    # Anomaly detection thresholds
    crowd_threshold: int = 5
    dwell_threshold_seconds: int = 30
    anomaly_zscore_threshold: float = 2.5

    # Store operating hours (24-hour, IST)
    store_open_hour: int = 11   # Brigade Road Purplle opens ~11am
    store_close_hour: int = 22  # Closes ~10pm

    # Camera
    camera_id: str = "CAM_FLOOR_01"
    camera_name: str = "Main Floor Camera"

    # Ingest limits
    max_ingest_batch_size: int = 500

    # Health: stale feed threshold in seconds
    stale_feed_threshold_seconds: int = 600  # 10 minutes

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
