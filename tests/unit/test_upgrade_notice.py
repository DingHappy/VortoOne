"""安装命令记下的升级结果：服务启动时进通知，成功和退回都说清楚，每个结果只报一次。"""
import json

from src.scheduling.notices import load_notices
from src.scheduling.upgrade_notice import report


def _result(tmp_path, **value):
    path = tmp_path / "upgrade.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def test_a_rollback_is_told_once_with_its_reason(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = _result(tmp_path, ok=False, **{"from": "0.2.64", "to": "0.2.65", "at": "2026-11-17T13:23:42Z",
                                          "reason": "服务启动后就退出了", "backup": "/b/x.tar.gz"})
    text = report(str(home), path)
    assert "没有成功" in text and "0.2.64" in text and "服务启动后就退出了" in text and "/b/x.tar.gz" in text
    assert [n["source"] for n in load_notices(str(home))] == ["upgrade"]
    assert report(str(home), path) is None                                    # 同一个结果不再报
    assert len(load_notices(str(home))) == 1


def test_a_success_is_told_and_a_newer_result_is_told_again(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = _result(tmp_path, ok=True, **{"from": "0.2.64", "to": "0.2.65", "at": "2026-11-17T10:00:00Z"})
    assert "已从 0.2.64 升级到 0.2.65" in report(str(home), path)
    path = _result(tmp_path, ok=True, **{"from": "0.2.65", "to": "0.2.66", "at": "2026-11-18T10:00:00Z"})
    assert "0.2.66" in report(str(home), path)


def test_nothing_to_tell_is_quiet(tmp_path, monkeypatch):
    monkeypatch.delenv("VORTOONE_UPGRADE_FILE", raising=False)
    assert report(str(tmp_path)) is None                                      # 不是用安装命令装的
    assert report(str(tmp_path), str(tmp_path / "missing.json")) is None
    (tmp_path / "bad.json").write_text("not json")
    assert report(str(tmp_path), str(tmp_path / "bad.json")) is None
    assert load_notices(str(tmp_path)) == []
