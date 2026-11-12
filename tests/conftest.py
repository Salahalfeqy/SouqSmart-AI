import os
os.environ["SOUQSMART_CATALOG_PROVIDER"] = "fixture"
os.environ["SOUQSMART_SERVICE_KEY"] = "test-service-key"
os.environ["SOUQSMART_INTENT_PARSER"] = "deterministic"
os.environ["GEMINI_API_KEY"] = ""
os.environ["SOUQSMART_ALLOW_ANONYMOUS_DEV_SESSIONS"] = "true"

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def headers():
    return {"X-Service-Key": "test-service-key"}
