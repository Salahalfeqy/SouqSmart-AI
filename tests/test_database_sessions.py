import asyncio
import os
import tempfile
import time
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.database import init_database, get_db_connection
from app.sessions_db import DatabaseSessionStore
from app.config import Settings
from app.main import app


@pytest.fixture
async def temp_db():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_chat.db")
    await init_database(db_path)
    yield db_path
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


@pytest.mark.anyio
async def test_session_lifecycle_and_turn_persistence(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0, turn_limit=5)
    
    # Client requests a new session with an initial ID that does not exist in DB:
    # Server MUST generate a fresh UUID and NOT store the arbitrary client string.
    sid_created, status_created = await store.get_or_create("client_specified_123", owner_hash="user_abc")
    assert sid_created != "client_specified_123"
    assert status_created == "created"

    # Subsequent access using the server-generated session ID and matching owner continues session
    sess_id_cont, status_cont = await store.get_or_create(sid_created, owner_hash="user_abc")
    assert sess_id_cont == sid_created
    assert status_cont == "continued"

    # Add turn 1
    t1 = await store.add_turn(
        session_id=sid_created,
        user="عايز لابتوب ديل",
        assistant="لقيت لك Dell XPS 13",
        rewritten_query="Dell XPS 13",
        intent_type="search",
        referenced_product_ids=["101", "102"],
        constraints={"brand": "Dell"},
    )
    assert t1 == 1

    turns = await store.get_turns(sid_created)
    assert len(turns) == 1
    assert turns[0]["turn_index"] == 1
    assert turns[0]["user"] == "عايز لابتوب ديل"
    assert turns[0]["assistant"] == "لقيت لك Dell XPS 13"
    assert turns[0]["referenced_product_ids"] == ["101", "102"]

    count = await store.get_turn_count(sid_created)
    assert count == 1


@pytest.mark.anyio
async def test_session_isolation_and_owner_mismatch(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0, turn_limit=10)
    sid, status = await store.get_or_create(None, owner_hash="owner_1")
    await store.add_turn(session_id=sid, user="Secret message", assistant="Reply")

    # Accessing with mismatched owner should generate a new server UUID
    sid2, status2 = await store.get_or_create(sid, owner_hash="owner_2")
    assert sid2 != sid
    assert status2 == "new"

    # New session should have 0 turns
    turns = await store.get_turns(sid2)
    assert len(turns) == 0


@pytest.mark.anyio
async def test_unknown_client_session_id_generates_server_uuid(temp_db):
    """Verify that arbitrary client-provided session IDs are rejected and never stored."""
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0)
    fake_client_id = "client_evil_injected_id_9999"

    new_sid, status = await store.get_or_create(fake_client_id, owner_hash="verified_customer_1")
    assert new_sid != fake_client_id
    assert status == "created"

    # Verify directly in SQLite that fake_client_id was NEVER inserted
    async with get_db_connection(temp_db) as db:
        async with db.execute("SELECT session_id FROM chat_sessions WHERE session_id = ?;", (fake_client_id,)) as cursor:
            row = await cursor.fetchone()
            assert row is None


@pytest.mark.anyio
async def test_max_turns_truncation(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0, turn_limit=3)
    sid, _ = await store.get_or_create(None, owner_hash="test_user")

    for i in range(5):
        await store.add_turn(
            session_id=sid,
            user=f"Message {i+1}",
            assistant=f"Reply {i+1}",
        )

    turns = await store.get_turns(sid)
    assert len(turns) == 3
    # Most recent 3 turns kept
    assert [t["user"] for t in turns] == ["Message 3", "Message 4", "Message 5"]


@pytest.mark.anyio
async def test_ttl_cleanup(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=0.1, turn_limit=10)
    sid, _ = await store.get_or_create(None, owner_hash="user_expire")
    await store.add_turn(session_id=sid, user="Will expire", assistant="Bye")

    # Sleep slightly to let ttl_seconds pass
    time.sleep(0.15)

    # Next get_or_create with expired sid should generate a fresh server session
    new_sid, status = await store.get_or_create(sid, owner_hash="user_expire")
    assert status in ("created", "new")
    assert new_sid != sid

    turns = await store.get_turns(new_sid)
    assert len(turns) == 0


@pytest.mark.anyio
async def test_concurrent_turn_insertion_prevents_duplicate_indices(temp_db):
    """Verify that concurrent turn insertions on the same session do not collide or produce duplicate indexes."""
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0, turn_limit=50)
    sid, _ = await store.get_or_create(None, owner_hash="concurrent_user")

    async def insert_worker(worker_id: int):
        return await store.add_turn(
            session_id=sid,
            user=f"Concurrent message {worker_id}",
            assistant=f"Reply {worker_id}",
        )

    # 10 workers calling add_turn simultaneously
    indices = await asyncio.gather(*[insert_worker(i) for i in range(10)])
    
    # Verify all returned indices are unique and sequentially 1..10
    assert sorted(indices) == list(range(1, 11))

    # Verify in DB
    turns = await store.get_turns(sid)
    assert len(turns) == 10
    db_indices = [t["turn_index"] for t in turns]
    assert db_indices == list(range(1, 11))


@pytest.mark.anyio
async def test_security_customer_cross_access_prevented(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0)
    
    # 1. Customer A creates session
    sid_a, status_a = await store.get_or_create(None, owner_hash="customer_A_hash")
    await store.add_turn(sid_a, user="Customer A secret question", assistant="Customer A answer")

    # 2. Customer B attempts to hijack Customer A's session ID
    sid_b, status_b = await store.get_or_create(sid_a, owner_hash="customer_B_hash")
    # Must reject session reuse and issue a new session
    assert sid_b != sid_a
    assert status_b == "new"

    # Customer B cannot see Customer A's turns
    turns_b = await store.get_turns(sid_b)
    assert len(turns_b) == 0


@pytest.mark.anyio
async def test_security_guest_cannot_access_authenticated_session(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0, allow_dev_anonymous=False)

    # Authenticated session
    auth_sid, _ = await store.get_or_create(None, owner_hash="authenticated_user_123")
    await store.add_turn(auth_sid, user="Order #999 query", assistant="Your order is shipped")

    # Guest attempt with literal "guest" must raise ValueError
    with pytest.raises(ValueError, match="owner_hash is required and cannot be 'guest'"):
        await store.get_or_create(auth_sid, owner_hash="guest")

    # Guest attempt with empty owner
    with pytest.raises(ValueError, match="owner_hash is required and cannot be 'guest'"):
        await store.get_or_create(auth_sid, owner_hash="")

    # Guest attempt with None owner
    with pytest.raises(ValueError, match="owner_hash is required and cannot be 'guest'"):
        await store.get_or_create(auth_sid, owner_hash=None)


@pytest.mark.anyio
async def test_security_guest_a_cannot_access_guest_b_session(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0)

    # Guest A connects with distinct anonymous identity
    sid_a, _ = await store.get_or_create(None, owner_hash="anon_guest_aaa")
    await store.add_turn(sid_a, user="Guest A message", assistant="Reply")

    # Guest B connects with different anonymous identity
    sid_b, status_b = await store.get_or_create(sid_a, owner_hash="anon_guest_bbb")
    assert sid_b != sid_a
    assert status_b == "new"


@pytest.mark.anyio
async def test_security_login_logout_isolation(temp_db):
    store = DatabaseSessionStore(db_path=temp_db, ttl_seconds=3600.0)

    # 1. User browses as guest
    guest_sid, _ = await store.get_or_create(None, owner_hash="anon_pre_login")
    await store.add_turn(guest_sid, user="Browsing laptops as guest", assistant="Showing laptops")

    # 2. User logs in -> owner identity changes to authenticated user hash
    post_login_sid, post_login_status = await store.get_or_create(guest_sid, owner_hash="auth_user_hash_77")
    # Must NEVER expose guest conversation under authenticated account without explicit merge
    assert post_login_sid != guest_sid
    assert post_login_status == "new"

    # 3. User logs out -> owner identity reverts to fresh anonymous hash
    post_logout_sid, post_logout_status = await store.get_or_create(post_login_sid, owner_hash="anon_post_logout")
    assert post_logout_sid != post_login_sid
    assert post_logout_status == "new"


# --- HTTP API Level Tests (FastAPI /v1/chat/messages) ---

def test_http_php_mode_rejects_missing_owner_hash():
    """Verify that in PHP-integrated mode, missing owner hash returns HTTP 403."""
    with patch("app.main.settings.catalog_provider", "php"):
        client = TestClient(app)
        headers = {"X-Service-Key": "test-service-key"}
        # No X-Owner-Hash sent
        resp = client.post("/v1/chat/messages", headers=headers, json={"message": "مرحبا"})
        assert resp.status_code == 403
        data = resp.json()
        assert "owner_hash is required in PHP-integrated mode" in data["error"]["message"]


def test_http_php_mode_rejects_empty_whitespace_and_guest_owner():
    """Verify that in PHP-integrated mode, empty, whitespace, and 'guest' are rejected with HTTP 403."""
    with patch("app.main.settings.catalog_provider", "php"):
        client = TestClient(app)
        
        for bad_owner in ["", "   ", "guest", "GUEST"]:
            headers = {"X-Service-Key": "test-service-key", "X-Owner-Hash": bad_owner}
            resp = client.post("/v1/chat/messages", headers=headers, json={"message": "مرحبا"})
            assert resp.status_code == 403, f"Expected 403 for owner_hash='{bad_owner}', got {resp.status_code}"


def test_http_php_mode_accepts_valid_php_hmac_owner():
    """Verify that a valid PHP-generated HMAC owner hash succeeds with HTTP 200."""
    with patch("app.main.settings.catalog_provider", "php"):
        client = TestClient(app)
        valid_php_hmac = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        headers = {"X-Service-Key": "test-service-key", "X-Owner-Hash": valid_php_hmac}
        resp = client.post("/v1/chat/messages", headers=headers, json={"message": "مرحبا"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["session_status"] == "created"
