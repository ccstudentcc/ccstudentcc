from __future__ import annotations

import sys
import json
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / ".github" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_common as wc  # type: ignore


def test_persistence_logging_and_metrics(tmp_path, monkeypatch):
    ROOT = wc.ROOT
    err_log = ROOT / ".github" / "manager" / "state" / "persistence-errors.log"
    metrics = ROOT / ".github" / "manager" / "state" / "persistence-metrics.json"
    # clean up from previous runs
    if err_log.exists():
        err_log.unlink()
    if metrics.exists():
        metrics.unlink()

    path = ROOT / ".github" / "manager" / "state" / "test-persist.json"
    if path.exists():
        path.unlink()

    # make save_json fail to exercise error path
    def fail_save(p, payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(wc, "save_json", fail_save)

    wc.enqueue_json(path, {"a": 1})
    wc.flush_json_writes(force=True)

    assert err_log.exists(), "persistence-errors.log should be created"
    content = err_log.read_text(encoding="utf-8")
    assert "Failed to persist" in content
    assert "boom" in content or "RuntimeError" in content

    assert metrics.exists(), "persistence-metrics.json should be created"
    m = json.loads(metrics.read_text(encoding="utf-8"))
    assert m.get("failed_writes", 0) >= 1
