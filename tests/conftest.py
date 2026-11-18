import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="pis-test-"))
os.environ["PIS_DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["PIS_UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["PIS_ADMIN_EMAIL"] = "admin@test.local"
os.environ["PIS_ADMIN_PASSWORD"] = "AdminPass123"
os.environ["PIS_SECRET_KEY"] = "test-secret-key-that-is-long-enough-1234"
os.environ["PIS_DISABLE_AI"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin@test.local", "AdminPass123")


@pytest.fixture(scope="session")
def make_user(client, admin):
    def _make(role):
        email = f"{role}@test.local"
        r = client.post("/api/users", headers=admin, json={"email": email, "password": "Password123", "role": role})
        assert r.status_code in (201, 409), r.text
        return _login(client, email, "Password123")
    return _make
