import os
import time

import pytest

from app.core.terminal import cleanup, spawn


def test_spawn_write_read_cleanup(tmp_path):
    session = spawn(str(tmp_path))
    os.write(session.fd, b"echo hi\n")
    time.sleep(0.3)
    out = os.read(session.fd, 4096)
    assert b"hi" in out
    cleanup(session)
    with pytest.raises(ProcessLookupError):
        os.kill(session.pid, 0)
