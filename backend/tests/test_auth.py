"""Unit and integration tests for authentication and credentials-based login."""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.anyio
async def test_login_super_admin_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@aloft.com", "password": "SuperAdmin123!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "admin@aloft.com"
        assert data["user"]["role"] == "SUPER_ADMIN"
        assert data["user"]["is_admin"] is True


@pytest.mark.anyio
async def test_login_ops_agent_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ops@aloft.com", "password": "OpsAgent123!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["role"] == "OPS_AGENT"
        assert data["user"]["is_admin"] is True


@pytest.mark.anyio
async def test_login_invalid_password():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@aloft.com", "password": "WrongPassword!"},
        )
        assert response.status_code == 401


@pytest.mark.anyio
async def test_auth_me_with_bearer_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": "admin@aloft.com", "password": "SuperAdmin123!"},
        )
        token = login_res.json()["access_token"]

        # 2. Query /me
        me_res = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["email"] == "admin@aloft.com"
        assert me_data["role"] == "SUPER_ADMIN"
