from carrot_leaf.engine import update


def test_version_compare_ignores_zero_padding():
    assert update._key("2026.08.19") == update._key("2026.8.19")
    assert update._key("2026.10.1") > update._key("2026.8.19")


def test_update_available(monkeypatch):
    monkeypatch.setattr(update, "installed_version", lambda: "2026.08.19")
    monkeypatch.setattr(update, "latest_version", lambda: "2026.9.1")
    assert update.update_available() == "2026.9.1"
    monkeypatch.setattr(update, "latest_version", lambda: None)
    assert update.update_available() is None


def test_upgrade_command_for_uv_tool(monkeypatch):
    monkeypatch.setattr(update.sys, "prefix", "/home/a/.local/share/uv/tools/carrot-leaf")
    monkeypatch.setattr(update.shutil, "which", lambda n: f"/bin/{n}" if n == "uv" else None)
    assert update.upgrade_command() == ["/bin/uv", "tool", "upgrade", "carrot-leaf"]


def test_self_upgrade_command(monkeypatch):
    which = lambda n: f"/bin/{n}"  # noqa: E731
    monkeypatch.setattr(update.shutil, "which", which)
    monkeypatch.setattr(update.sys, "prefix", "/home/a/.local/share/uv/tools/carrot-leaf")
    assert update.self_upgrade_command() == ["/bin/uv", "tool", "upgrade", "carrot-leaf"]
    monkeypatch.setattr(update.sys, "prefix", "/home/a/.local/pipx/venvs/carrot-leaf")
    assert update.self_upgrade_command() == ["/bin/pipx", "upgrade", "carrot-leaf"]
    monkeypatch.setattr(update.sys, "prefix", "/home/a/project/.venv")
    assert update.self_upgrade_command() is None


def test_app_update_available(monkeypatch):
    import carrot_leaf

    monkeypatch.delenv("CARROT_NO_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(carrot_leaf, "__version__", "0.2.0")
    monkeypatch.setattr(update, "self_upgrade_command", lambda: ["uv", "tool", "upgrade"])
    monkeypatch.setattr(update, "latest_version", lambda url=None, timeout=4.0: "0.10.0")
    assert update.app_update_available() == "0.10.0"
    monkeypatch.setattr(update, "latest_version", lambda url=None, timeout=4.0: "0.2.0")
    assert update.app_update_available() is None
    monkeypatch.setattr(update, "latest_version", lambda url=None, timeout=4.0: "0.10.0")
    monkeypatch.setenv("CARROT_NO_AUTO_UPDATE", "1")
    assert update.app_update_available() is None
    monkeypatch.delenv("CARROT_NO_AUTO_UPDATE")
    monkeypatch.setattr(update, "self_upgrade_command", lambda: None)
    assert update.app_update_available() is None
