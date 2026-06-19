"""Тести команди запуску лаунчера в апдейтері."""

from __future__ import annotations

from updater import launcher_cmd


def test_launcher_cmd_prefers_python_over_pythonw(tmp_path):
    # pythonw.exe запускав лаунчер/streamlit ненадійно (тихе падіння); беремо
    # python.exe, консоль ховає CREATE_NO_WINDOW.
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("")
    (scripts / "pythonw.exe").write_text("")
    cmd = launcher_cmd(tmp_path)
    assert cmd[0].endswith("python.exe")
    assert not cmd[0].endswith("pythonw.exe")
    assert cmd[1].endswith("launch_dashboard.py")


def test_launcher_cmd_falls_back_to_uv_without_venv(tmp_path):
    cmd = launcher_cmd(tmp_path)
    assert cmd[0] == "uv"
    assert cmd[-1].endswith("launch_dashboard.py")
