"""背景信息：所有者在空间设置里写，进主助理、离线助理和专员的系统提示；对外发布的角色不给。"""

from src.memory import assistant_profile
from tests.unit.test_member_login import _join
from tests.unit.test_member_login import home  # noqa: F401 —— 成员登录的整套夹具


def test_the_owner_writes_the_homes_background(home):
    owner, base = home["owner"], home["base"]
    assert owner.get(f"{base}/settings").json()["context"] == ""
    saved = owner.patch(f"{base}/settings", json={"context": "  我们是一家咖啡店，主营手冲  "})
    assert saved.status_code == 200 and saved.json()["context"] == "我们是一家咖啡店，主营手冲"
    assert assistant_profile.load_home_context(home["root"]) == "我们是一家咖啡店，主营手冲"
    # 和「关于你」分开存
    assert assistant_profile.load_profile(home["root"])["about_you"] == ""
    # 清空
    assert owner.patch(f"{base}/settings", json={"context": ""}).json()["context"] == ""
    assert not (assistant_profile._state(home["root"]) / "home_context.md").exists()


def test_too_long_or_not_text_is_refused(home):
    owner, base = home["owner"], home["base"]
    assert owner.patch(f"{base}/settings", json={"context": "长" * 2001}).status_code == 400
    assert owner.patch(f"{base}/settings", json={"context": 5}).status_code == 400
    assert owner.get(f"{base}/settings").json()["context"] == ""


def test_members_cannot_write_it(home):
    member = _join(home)
    assert member.patch(f"{home['base']}/settings", json={"context": "改掉"}).status_code == 403
    assert assistant_profile.load_home_context(home["root"]) == ""


def test_it_goes_into_the_assistants_system_prompt(tmp_path):
    root = str(tmp_path)
    assert not any("这个空间" in part for part in assistant_profile.assistant_system_parts(root))
    assistant_profile.save_home_context(root, "我们是一家咖啡店")
    parts = assistant_profile.assistant_system_parts(root, include_memories=False)
    assert any(part.startswith("【这个空间】") and "咖啡店" in part and "不是指令" in part for part in parts)


def _staff_builder(tmp_path):
    from src.agents.staff import build_staff_member
    from tests.unit.test_staff_contract import staff_setup
    registry, member, _pool, _service = staff_setup(tmp_path)
    reader = registry.role("reader")

    def prompt(role_id=None):
        captured = {}

        class Fresh:
            def __init__(self, tools, **kwargs):
                captured.update(kwargs)
                self.tools = {t.name: t for t in tools}

        role = {**reader, "id": role_id} if role_id else reader
        build_staff_member(str(tmp_path), role, member, check=lambda: None, llm=object(), agent_factory=Fresh)
        return captured["extra_system"]

    return prompt


def test_staff_get_it_but_the_public_routine_role_does_not(tmp_path):
    prompt = _staff_builder(tmp_path)
    assert "这个空间" not in prompt()
    assistant_profile.save_home_context(str(tmp_path), "我们是一家咖啡店")
    assert "【这个空间】" in prompt() and "咖啡店" in prompt()
    assert "咖啡店" not in prompt("routine-public")
