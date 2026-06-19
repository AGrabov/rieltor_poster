"""Тести побудови команди запуску Streamlit у лаунчері."""

from __future__ import annotations

from launch_dashboard import streamlit_cmd


def test_streamlit_cmd_prefers_venv_python_over_pythonw(tmp_path):
    # python.exe надійніший за pythonw.exe для streamlit; консоль ховаємо
    # через CREATE_NO_WINDOW, тож pythonw не потрібен.
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("")
    (scripts / "pythonw.exe").write_text("")
    cmd = streamlit_cmd(tmp_path)
    assert cmd[0].endswith("python.exe")
    assert not cmd[0].endswith("pythonw.exe")
    assert cmd[1:5] == ["-m", "streamlit", "run", "dashboard.py"]


def test_streamlit_cmd_falls_back_to_uv_without_venv(tmp_path):
    cmd = streamlit_cmd(tmp_path)  # .venv відсутня
    assert cmd[0] == "uv"
    assert "streamlit" in cmd
