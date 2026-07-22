from desktop.runtime import ensure_environment, find_available_port


def test_environment_is_created(tmp_path) -> None:
    env = ensure_environment(tmp_path)
    assert env.is_file()
    assert (tmp_path / "data" / "artifacts").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert "OPENSLT_API_PORT=8765" in env.read_text(encoding="utf-8")


def test_port_selection_returns_available_port() -> None:
    port = find_available_port(0)
    assert 1 <= port <= 65535
