"""Bounded handoff frames use only the paused browser and never save an image."""
import asyncio

import pytest

from src.browser.handoff_preview import BrowserHandoffFrame
from src.browser.page_privacy import HANDOFF_CONTROL_STATE_JS, HANDOFF_PREVIEW_STATE_JS
from tests.unit.test_computer_browser_preview import _image


class Window:
    def __init__(self, **state):
        self.state = {"url": "https://example.test/ready", "document": 1000, "protected": False, "frames": False} | state
        self.frames = 0
        self.changed = {}

    async def evaluate(self, program):
        assert program == HANDOFF_PREVIEW_STATE_JS
        return dict(self.state)

    async def screenshot(self):
        self.frames += 1
        self.state.update(self.changed)
        return _image()


@pytest.mark.parametrize("state", [
    {"protected": True}, {"frames": True}, {"document": None},
    {"url": "https://example.test/%6cogin.php"}, {"url": "https://example.test/ready?code=private"},
    {"url": "https://example.test/page#private"}, {"url": "https://other.test/ready"},
    {"url": "http://example.test/ready"},
])
async def test_handoff_does_not_capture_protected_or_unbound_pages(state):
    browser = Window(**state)
    preview = BrowserHandoffFrame(browser, "https://example.test", asyncio.Lock(), lambda: True)
    assert await preview.pull(lambda: True) is None
    assert browser.frames == 0


@pytest.mark.parametrize("change", [{"document": 1001}, {"protected": True}, {"url": "https://other.test/ready"}])
async def test_handoff_drops_the_frame_if_the_document_or_privacy_check_changes(change):
    browser = Window()
    browser.changed = change
    preview = BrowserHandoffFrame(browser, "https://example.test", asyncio.Lock(), lambda: True)
    assert await preview.pull(lambda: True) is None
    assert browser.frames == 1


async def test_handoff_exposes_only_a_png_and_paces_requests():
    browser = Window()
    preview = BrowserHandoffFrame(browser, "https://example.test", asyncio.Lock(), lambda: True)
    assert await preview.pull(lambda: True) == _image()
    assert await preview.pull(lambda: True) is None
    assert browser.frames == 1


@pytest.mark.parametrize("changed", [
    {}, {"document": 1001}, {"scrollY": 12}, {"protected": True}, {"allowed": False},
])
async def test_input_requires_one_fresh_matching_frame_and_allowed_field(changed):
    widget = Window(width=960, height=600, scrollX=0, scrollY=0)
    typed = []

    async def state(program):
        if program == HANDOFF_PREVIEW_STATE_JS:
            return dict(widget.state)
        assert program.startswith(HANDOFF_CONTROL_STATE_JS)
        assert "plain" not in program
        return {"allowed": True, **widget.state, **changed}

    async def insert(text):
        typed.append(text)

    widget.insert_text = insert
    preview = BrowserHandoffFrame(widget, "https://example.test", asyncio.Lock(), lambda: True, evaluate=state)
    action = {"kind": "text", "text": "plain"}
    assert await preview.interact(action) is False
    assert await preview.pull(lambda: True) == _image()
    assert await preview.interact(action) is (not changed)
    assert await preview.interact(action) is False
    assert typed == ([] if changed else ["plain"])
