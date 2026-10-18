from carrot_leaf import config


def test_roundtrip(tmp_path):
    path = tmp_path / "c.toml"
    s = config.Settings(download_dir=str(tmp_path), last_kind="audio", cookies_browser="firefox")
    config.save(s, path)
    loaded = config.load(path)
    assert loaded == s


def test_missing_or_broken_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.toml").download_dir == config.default_download_dir()
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml")
    assert config.load(bad) == config.Settings()


def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('download_dir = "/x"\nsomething_new = "y"\n')
    assert config.load(p).download_dir == "/x"
