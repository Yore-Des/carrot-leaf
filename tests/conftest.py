import pytest

from carrot_leaf import config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep tests away from the real config file and Downloads folder."""
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config, "default_download_dir", lambda: str(tmp_path / "downloads"))
    monkeypatch.setenv("CARROT_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("CARROT_NO_FFMPEG_FETCH", "1")
    return tmp_path
