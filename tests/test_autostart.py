import sys
import xml.etree.ElementTree as ET

from winduo2 import autostart

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


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


class TestTask:
    def _task(self, monkeypatch, tmp_path):
        exe = tmp_path / "Win & Duo" / "WinDuo2.exe"
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        text = autostart.task_xml(user="LAPTOP\\minjoon")
        # Parsed as the UTF-16 file schtasks reads.
        return ET.fromstring(text.encode("utf-16")), exe

    def test_starts_this_user_at_sign_in(self, monkeypatch, tmp_path):
        task, _ = self._task(monkeypatch, tmp_path)
        assert task.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text == "LAPTOP\\minjoon"
        principal = task.find("t:Principals/t:Principal", NS)
        assert principal.find("t:RunLevel", NS).text == "LeastPrivilege"

    def test_runs_this_exe_with_the_startup_flag(self, monkeypatch, tmp_path):
        task, exe = self._task(monkeypatch, tmp_path)
        assert task.find("t:Actions/t:Exec/t:Command", NS).text == str(exe)
        assert task.find("t:Actions/t:Exec/t:Arguments", NS).text == autostart.FLAG

    def test_overrides_the_defaults_that_would_stop_the_app(self, monkeypatch, tmp_path):
        settings, _ = self._task(monkeypatch, tmp_path)
        settings = settings.find("t:Settings", NS)
        # Task Scheduler would otherwise stop it after three days, refuse to
        # start it on battery, and run it below normal priority.
        assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S"
        assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
        assert settings.find("t:StopIfGoingOnBatteries", NS).text == "false"
        assert settings.find("t:Priority", NS).text == "4"


class TestSync:
    def _record(self, monkeypatch, registers: bool):
        calls = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(autostart, "_register_task", lambda: calls.append("task") or registers)
        monkeypatch.setattr(autostart, "_delete_task", lambda: calls.append("no task"))
        monkeypatch.setattr(
            autostart, "_set_run_entry", lambda on: calls.append("run" if on else "no run")
        )
        return calls

    def test_prefers_the_task_and_clears_the_old_run_entry(self, monkeypatch):
        calls = self._record(monkeypatch, registers=True)
        autostart.sync(True)
        assert calls == ["task", "no run"]

    def test_falls_back_to_the_run_key(self, monkeypatch):
        calls = self._record(monkeypatch, registers=False)
        autostart.sync(True)
        assert calls == ["task", "run"]

    def test_off_removes_both(self, monkeypatch):
        calls = self._record(monkeypatch, registers=True)
        autostart.sync(False)
        assert calls == ["no task", "no run"]
