import os
import sys
import tempfile
from pathlib import Path

# isolate all test data BEFORE the app modules are imported
_TMP = tempfile.mkdtemp(prefix="bookmaker-test-")
os.environ["BOOKMAKER_DATA_DIR"] = _TMP
os.environ["BOOKMAKER_START_WORKER"] = "0"
os.environ["BOOKMAKER_RATE_LIMIT_PER_MINUTE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app_client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def worker():
    from app.pipeline.worker import Worker
    w = Worker(max_books=1)
    yield w
    w.stop()


@pytest.fixture(scope="session")
def mock_server():
    from tests.mock_servers import start
    srv = start()
    yield srv
    srv.stop()
