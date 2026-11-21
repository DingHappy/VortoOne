"""本服务把新版界面当作自己的网站提供：只放静态文件和界面的页面地址，数据接口的鉴权不变。"""

import pytest
from fastapi.testclient import TestClient

TOKEN = "owner-token-for-test"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "VORTOONE_APP_UI_DIR"):
        monkeypatch.delenv(name, raising=False)
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html>界面</html>", encoding="utf-8")
    (ui / "assets" / "main-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (ui / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不在界面目录里", encoding="utf-8")
    from src.web.server import app
    return {"client": TestClient(app), "ui": ui}


def test_without_the_setting_nothing_changes(site):
    client = site["client"]
    assert client.get("/").json()["message"] == "VortoOne API"
    assert client.get("/login").status_code == 404
    assert client.get("/assets/main-abc123.js").status_code == 404


def test_pages_and_files_are_served_before_sign_in(site, monkeypatch):
    monkeypatch.setenv("VORTOONE_APP_UI_DIR", str(site["ui"]))
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    client = site["client"]
    for page in ("/", "/login", "/settings/skills", "/desktop/handover"):
        response = client.get(page)
        assert response.status_code == 200 and "界面" in response.text, page
        assert response.headers["cache-control"] == "no-cache"
    script = client.get("/assets/main-abc123.js")
    assert script.status_code == 200 and script.text == "console.log(1)"
    assert "immutable" in script.headers["cache-control"]
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/assets/missing.js").status_code in {401, 404}       # 缺失的文件不回页面
    assert "界面" not in client.get("/assets/missing.js").text


def test_data_routes_and_existing_pages_keep_their_rules(site, monkeypatch):
    monkeypatch.setenv("VORTOONE_APP_UI_DIR", str(site["ui"]))
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    client = site["client"]
    assert client.get("/api/app/me").status_code == 401
    assert client.get("/api/does-not-exist").status_code == 401
    assert client.get("/api/instance").json()["name"] == "VortoOne"
    assert client.get("/artifact/anything").status_code == 401                # 已有页面仍要登录
    assert "界面" not in client.get("/review").text
    assert client.post("/login").status_code in {401, 404, 405}               # 只有读取才回页面
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    assert client.get("/api/app/me").status_code == 200


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt",
                                  "/assets/%2e%2e/%2e%2e/secret.txt"])
def test_files_outside_the_ui_folder_are_never_served(site, monkeypatch, path):
    monkeypatch.setenv("VORTOONE_APP_UI_DIR", str(site["ui"]))
    assert "不在界面目录里" not in site["client"].get(path).text


def test_a_symlink_out_of_the_folder_is_not_followed(site, monkeypatch, tmp_path):
    monkeypatch.setenv("VORTOONE_APP_UI_DIR", str(site["ui"]))
    (site["ui"] / "leak.txt").symlink_to(tmp_path / "secret.txt")
    assert "不在界面目录里" not in site["client"].get("/leak.txt").text


def test_a_folder_without_an_index_is_ignored(site, monkeypatch, tmp_path):
    monkeypatch.setenv("VORTOONE_APP_UI_DIR", str(tmp_path / "nope"))
    assert site["client"].get("/").json()["message"] == "VortoOne API"
