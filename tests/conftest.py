"""Pytest configuration for the Store Intelligence System test suite."""
import sys
import os

# Build paths
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_backend_dir = os.path.join(_repo_root, 'backend')

# Backend first so that 'pipeline' resolves to backend/pipeline (for API imports)
# test_pipeline.py overrides this locally with repo root first
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _repo_root not in sys.path:
    sys.path.insert(1, _repo_root)  # root at index 1, backend at 0

# Use a test SQLite database
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_store_intelligence.db")

import unittest.mock as mock

# Patch pipeline startup — path is relative to backend/ perspective
_start_patcher = mock.patch('pipeline.video_processor.video_processor.start', return_value=None)
_stop_patcher = mock.patch('pipeline.video_processor.video_processor.stop', return_value=None)
_start_patcher.start()
_stop_patcher.start()

# Initialise DB tables before any test runs
from database import init_db  # noqa: E402
from models import event, person, anomaly  # noqa: E402
import models.pos_transaction  # noqa: E402

init_db()
