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
