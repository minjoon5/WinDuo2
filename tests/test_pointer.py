"""The pointer has to be hidden for the effect and given back, once."""

from __future__ import annotations

import pytest

from winduo2.render import pointer
from winduo2.render.pointer import PointerHider


class _FakeWin32:
    def __init__(self, hides=True):
        self.hides = hides
        self.hidden = 0
        self.restored = 0

    def hide_pointer(self):
        self.hidden += 1
        return self.hides

    def restore_pointer(self):
        self.restored += 1


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs):
        stub = _FakeWin32(**kwargs)
        monkeypatch.setattr(pointer, "win32", stub)
        monkeypatch.setattr(pointer.atexit, "register", lambda _f: None)
        return stub

    return install


def test_hidden_for_the_run_and_restored_once(fake):
    stub = fake()
    hider = PointerHider()
    hider.begin()
    hider.begin()
    assert hider.is_hidden and stub.hidden == 1
    hider.end()
    hider.end()
    assert not hider.is_hidden and stub.restored == 1


def test_nothing_to_restore_when_hiding_failed(fake):
    stub = fake(hides=False)
    hider = PointerHider()
    hider.begin()
    hider.end()
    assert not hider.is_hidden and stub.restored == 0
