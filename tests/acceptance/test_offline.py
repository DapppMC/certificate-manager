"""T-015/T-018 no network use (R-22): a run must succeed with all sockets blocked."""
import socket

import pytest
from conftest import DB, FIX, cli, run_dir_from


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise OSError("network blocked by test_offline")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)


def test_text_run_offline(no_network, tmp_path, capsys):
    code, so, se = cli(["run", "--pdf", str(FIX / "bundle_simple.pdf"), "--db", str(DB), "--out", str(tmp_path),
                        "--no-ocr"], capsys)
    assert code == 0, so + se
    assert len(list((run_dir_from(so) / "pdf").glob("*.pdf"))) == 3
