import sys

from winduo2 import autostart


def test_frozen_exe_runs_itself(monkeypatch, tmp_path):
    exe = tmp_path / "WinDuo2.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.command() == f'"{exe}" --autostart'


def test_source_install_prefers_pythonw(monkeypatch, tmp_path):
    (tmp_path / "pythonw.exe").touch()
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.command() == f'"{tmp_path / "pythonw.exe"}" -m winduo2 --autostart'


def test_sync_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    autostart.sync(True)
    autostart.sync(False)


def test_the_startup_flag_is_one_main_accepts():
    from winduo2.main import _parse

    assert _parse([autostart.FLAG]).autostart
    assert not _parse([]).autostart
