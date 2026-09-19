"""HTTP 接口测试：健康检查、版本信息与规则数据。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import __version__
from backend.config import Settings
from backend.web.app import create_app


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(prepare_timeout=5))
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client: TestClient):
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["rooms"] == 0
    assert body["totals"]["rooms_created"] >= 0


def test_frontend_reponses_carry_basic_security_headers(client: TestClient):
    response = client.get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize(
    ("path", "must_contain"),
    [
        ("/", "星陨竞技场"),
        ("/about", "核心乐趣"),
        ("/rules", "标签效果"),
    ],
)
def test_pages_are_served_with_asset_version(client: TestClient, path: str, must_contain: str):
    """主页面、介绍页、规则页都要能打开，并且把 {{ASSET_VERSION}} 换成真实指纹。"""

    response = client.get(path)

    assert response.status_code == 200
    html = response.text
    assert must_contain in html
    assert "{{ASSET_VERSION}}" not in html          # 模板占位符必须被替换
    assert "?v=" in html                            # 静态资源带版本号，不吃缓存
    assert response.headers["content-type"].startswith("text/html")


def test_about_page_links_to_rules_and_repo(client: TestClient):
    html = client.get("/about").text

    assert 'href="/rules"' in html
    assert "github.com/CuberRobot/NeoNovaClash" in html


def test_static_assets_are_revalidated(client: TestClient):
    """发版后浏览器与 CDN 必须回源校验，不能继续用旧的 JS/CSS。"""

    response = client.get("/static/js/app.js")

    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]


def test_version_endpoint(client: TestClient):
    body = client.get("/api/version").json()

    assert body["name"] == "NeoNovaClash"
    assert body["version"] == __version__
    assert body["mode"] == "standard"


def test_rules_endpoint_matches_game_data(client: TestClient):
    body = client.get("/api/rules").json()

    assert body["constants"]["pool_size"] == 6
    assert body["constants"]["team_size"] == 3
    assert len(body["characters"]) == 20
    assert any(tag["key"] == "explosive" for tag in body["tags"])
    assert len(body["strategies"]) == 3


def test_index_page_is_served(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "星陨竞技场" in response.text
