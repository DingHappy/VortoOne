"""语音消息：语音服务的 Key、上传与转写、团队频道和主助理私聊里的语音条、回放权限。服务商一律打桩。"""

import json
import os
import stat

import pytest

from src.web import app_voice
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


@pytest.fixture
def speech(monkeypatch):
    """假的语音服务：记下请求，按路径回答。"""
    state = {"text": "明天上午十点开会", "status": 200, "probe": 200}
    calls = []

    async def fake_http(method, url, *, headers, form=None, timeout=60):
        calls.append((method, url, dict(headers)))
        if method == "GET":
            return state["probe"], {}
        return state["status"], {"text": state["text"]} if state["status"] < 400 else {"error": "x"}

    monkeypatch.setattr(app_voice, "_http", fake_http)
    return {"state": state, "calls": calls}


@pytest.fixture
def bridge(monkeypatch):
    """主要聊天：只记下交给它的文字和回合号。"""
    posted = []

    class Fake:
        async def post(self, text, client_id, *, rid=None, **_flags):
            posted.append((text, client_id, rid))
            return {}

    async def fake_bridge(root):
        return Fake()

    monkeypatch.setattr("src.web.routers.app_api.main_chat_bridge", fake_bridge)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")            # model_connected
    return posted


def _key(home, **body):
    answer = home["owner"].put("/api/app/speech-key", json={"provider": "openai", "apiKey": "sk-abcd1234", **body})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _send(client, url, *, client_id="c1", audio=WEBM, duration="1500", peaks="[0.1, 0.8]", **extra):
    data = {"durationMs": duration, "peaks": peaks, "clientId": client_id, **extra}
    return client.post(f"{url}/voice", files={"file": ("voice.webm", audio, "audio/webm")}, data=data)


# ── Key ───────────────────────────────────────────────────────────────────────

def test_the_owner_sets_a_speech_provider(home, speech):
    owner, base = home["owner"], home["base"]
    assert owner.get("/api/app/speech-key").json() == {"provider": None, "hint": None, "state": "off",
                                                       "envProviders": []}
    assert owner.get(f"{base}/settings").json()["voiceAvailable"] is False
    assert _key(home) == {"provider": "openai", "hint": "1234", "state": "ok", "envProviders": []}
    method, url, headers = speech["calls"][0]
    assert (method, url) == ("GET", "https://api.openai.com/v1/models") and headers["Authorization"] == "Bearer sk-abcd1234"
    settings = owner.get(f"{base}/settings").json()
    assert settings["voiceAvailable"] is True and settings["voiceMessages"] is True
    assert owner.get(f"{base}/voice/allowed").json() == {"allowed": True}
    assert "sk-abcd1234" not in owner.get("/api/app/speech-key").text
    assert stat.S_IMODE(os.stat(app_voice.provider_keys.path(home["root"])).st_mode) == 0o600
    assert owner.patch(f"{base}/settings", json={"voiceMessages": False}).json()["voiceMessages"] is False
    assert owner.get(f"{base}/voice/allowed").json() == {"allowed": False}
    assert owner.delete("/api/app/speech-key").json()["state"] == "off"


def test_a_local_server_and_elevenlabs(home, speech):
    assert _key(home, provider="local", apiKey=None, baseUrl="http://127.0.0.1:9000/v1/")["hint"] == "127.0.0.1:9000"
    assert speech["calls"][-1][1] == "http://127.0.0.1:9000/v1/models"
    assert _key(home, provider="elevenlabs", apiKey="el-key-9999")["provider"] == "elevenlabs"
    assert speech["calls"][-1][1] == "https://api.elevenlabs.io/v1/user"
    assert speech["calls"][-1][2] == {"xi-api-key": "el-key-9999"}


@pytest.mark.parametrize("body, code", [
    ({"provider": "assemblyai", "apiKey": "x"}, "voice.provider_unsupported"),
    ({"provider": "soniox", "apiKey": "x"}, "voice.provider_unsupported"),
    ({"provider": "openai"}, "invalid"),
    ({"provider": "local", "baseUrl": "file:///etc/passwd"}, "invalid"),
    ({"provider": "openai", "apiKey": "x", "baseUrl": "http://elsewhere"}, "invalid"),
    ({"provider": "whatever", "apiKey": "x"}, "invalid"),
])
def test_bad_speech_settings_are_refused(home, speech, body, code):
    answer = home["owner"].put("/api/app/speech-key", json=body)
    assert answer.status_code == 400 and answer.json()["error"]["code"] == code
    assert home["owner"].get("/api/app/speech-key").json()["state"] == "off"


def test_a_key_the_provider_refuses_is_not_kept(home, speech):
    speech["state"]["probe"] = 401
    answer = home["owner"].put("/api/app/speech-key", json={"provider": "groq", "apiKey": "gsk-bad"})
    assert answer.status_code == 400 and answer.json()["error"]["code"] == "voice.key_invalid"
    assert home["owner"].get("/api/app/speech-key").json()["state"] == "off"


def test_members_cannot_touch_the_speech_key(home, speech):
    member = _join(home)
    for call in (member.get("/api/app/speech-key"), member.delete("/api/app/speech-key"),
                 member.put("/api/app/speech-key", json={"provider": "openai", "apiKey": "x"})):
        assert call.status_code == 403


# ── 团队频道 ─────────────────────────────────────────────────────────────────

def test_a_voice_message_in_a_team_channel(home, speech):
    _key(home)
    owner, member = home["owner"], _join(home)
    url = _create(home, "standup")
    assert member.post(f"{url}/join").status_code == 204
    sent = _send(member, url, language="zh")
    assert sent.status_code == 200, sent.text
    clip = sent.json()["clipId"]
    post = speech["calls"][-1]
    assert post[0] == "POST" and post[1] == "https://api.openai.com/v1/audio/transcriptions"
    [message] = owner.get(f"{url}/messages").json()["messages"]
    assert message["body"] == "明天上午十点开会"                         # 文字给 agent 看
    voice = message["meta"]["voice"]
    assert voice["clipId"] == clip and voice["durationMs"] == 1500 and voice["peaks"] == [0.1, 0.8]
    assert voice["expiresAt"] > message["createdAt"]
    # 看得到这个频道的人能听
    for client in (owner, member):
        audio = client.get(f"{home['base']}/voice/{clip}/audio")
        assert audio.status_code == 200 and audio.content == WEBM and audio.headers["content-type"] == "audio/webm"
    # 重试同一条：不再转写、不重复发
    calls = len(speech["calls"])
    assert _send(member, url).json() == {"clipId": clip}
    assert len(speech["calls"]) == calls and len(owner.get(f"{url}/messages").json()["messages"]) == 1


def test_someone_outside_the_channel_cannot_listen(home, speech):
    _key(home)
    owner = home["owner"]
    url = _create(home, "secret", visibility="private")
    clip = _send(owner, url).json()["clipId"]
    outsider = _join(home, email="bo@example.com", name="小博")
    assert outsider.get(f"{home['base']}/voice/{clip}/audio").status_code == 404
    assert _send(outsider, url, client_id="x").status_code in (403, 404)


def test_when_the_engine_fails_people_can_still_listen(home, speech):
    _key(home)
    owner = home["owner"]
    url = _create(home, "standup")
    speech["state"]["status"] = 500
    assert _send(owner, url).status_code == 200
    [message] = owner.get(f"{url}/messages").json()["messages"]
    assert message["body"] == "" and message["meta"]["voice"]["noWords"] is True


def test_silence_is_not_a_message(home, speech):
    _key(home)
    url = _create(home, "standup")
    speech["state"]["text"] = "   "
    answer = _send(home["owner"], url)
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "voice.silent"
    assert home["owner"].get(f"{url}/messages").json()["messages"] == []
    folder = app_voice._dir(home["root"])
    assert not list(folder.glob("*.audio"))                             # 录音也没留下


@pytest.mark.parametrize("kwargs, code", [
    ({"audio": b"not audio at all"}, "voice.format"),
    ({"duration": "100"}, "voice.length"),
    ({"duration": str(6 * 60_000)}, "voice.length"),
    ({"peaks": "[2]"}, "invalid"),
    ({"peaks": json.dumps([0.1] * 49)}, "invalid"),
    ({"client_id": ""}, "invalid"),
])
def test_bad_recordings_are_refused(home, speech, kwargs, code):
    _key(home)
    url = _create(home, "standup")
    answer = _send(home["owner"], url, **kwargs)
    assert answer.status_code == 400 and answer.json()["error"]["code"] == code
    assert not [c for c in speech["calls"] if c[0] == "POST"]          # 没去转写


def test_too_big_a_recording_is_refused_before_reading_it_all(home, speech, monkeypatch):
    _key(home)
    monkeypatch.setattr("src.web.routers.app_api._MAX_VOICE_BODY", 1024)
    url = _create(home, "standup")
    answer = _send(home["owner"], url, audio=WEBM + b"\x00" * 4096)
    assert answer.status_code == 413


def test_voice_is_off_until_a_provider_is_set(home, speech):
    url = _create(home, "standup")
    answer = _send(home["owner"], url)
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "voice.off"


def test_expired_recordings_answer_410(home, speech):
    _key(home)
    owner = home["owner"]
    url = _create(home, "standup")
    clip = _send(owner, url).json()["clipId"]
    path = app_voice._dir(home["root"]) / f"{clip}.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "expiresAt": "2000-01-01T00:00:00Z"}))
    answer = owner.get(f"{home['base']}/voice/{clip}/audio")
    assert answer.status_code == 410 and answer.json()["error"]["code"] == "voice.expired"


# ── 主助理私聊 ───────────────────────────────────────────────────────────────

def test_a_voice_message_to_the_main_assistant(home, speech, bridge):
    from src.web import app_views
    _key(home)
    owner, base = home["owner"], home["base"]
    main = owner.post(f"{base}/dms", json={"agentId": home["main"]["id"]}).json()["channelId"]
    sent = _send(owner, f"{base}/channels/{main}", client_id="v1")
    assert sent.status_code == 200, sent.text
    [(text, client_id, rid)] = bridge
    assert (text, client_id) == ("明天上午十点开会", "v1") and rid
    # 这条消息读出来带着语音条
    row = app_views.owner_message(home["root"], rid, text, client_id=client_id)
    assert row["meta"]["voice"]["clipId"] == sent.json()["clipId"]
    assert owner.get(f"{base}/voice/{sent.json()['clipId']}/audio").status_code == 200


def test_the_main_assistant_cannot_get_a_voice_it_cannot_hear(home, speech, bridge):
    _key(home)
    owner, base = home["owner"], home["base"]
    main = owner.post(f"{base}/dms", json={"agentId": home["main"]["id"]}).json()["channelId"]
    speech["state"]["status"] = 500
    answer = _send(owner, f"{base}/channels/{main}")
    assert answer.status_code == 502 and bridge == []


def test_voice_only_goes_to_the_main_assistant_and_team_channels(home, speech, bridge):
    _key(home)
    owner = home["owner"]
    answer = _send(owner, f"{home['base']}/channels/{home['channel']}")         # 专员私聊
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "voice.off_here"


def test_a_member_cannot_speak_to_the_main_assistant(home, speech, bridge):
    _key(home)
    member = _join(home)
    owner, base = home["owner"], home["base"]
    main = owner.post(f"{base}/dms", json={"agentId": home["main"]["id"]}).json()["channelId"]
    assert _send(member, f"{base}/channels/{main}").status_code == 403
    assert bridge == []


def test_multipart_parsing_takes_fields_and_the_file():
    body = (b"--b\r\nContent-Disposition: form-data; name=\"clientId\"\r\n\r\nc1\r\n"
            b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"v.webm\"\r\n"
            b"Content-Type: audio/webm\r\n\r\n" + WEBM + b"\r\n--b--\r\n")
    fields, upload = app_voice.parse_form("multipart/form-data; boundary=b", body)
    assert fields == {"clientId": "c1"} and upload == (WEBM, "audio/webm")
    with pytest.raises(app_voice.VoiceError):
        app_voice.parse_form("application/json", b"{}")


def test_origin_is_checked_for_member_voice(home, speech):
    _key(home)
    member = _join(home)
    url = _create(home, "standup")
    member.post(f"{url}/join")
    answer = member.post(f"{url}/voice", files={"file": ("v.webm", WEBM, "audio/webm")},
                         data={"durationMs": "1500", "peaks": "[]", "clientId": "o1"},
                         headers={"Origin": "http://evil.example"})
    assert answer.status_code == 403 and ORIGIN["Origin"] != "http://evil.example"
