import pytest

from maildb.ingest.tasks import (
    claim_task,
    complete_task,
    create_task,
    fail_task,
    get_phase_status,
    reset_failed_tasks,
)

pytestmark = pytest.mark.integration


def test_create_task(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_001.mbox")
    assert task["id"] is not None
    assert task["phase"] == "parse"
    assert task["status"] == "pending"
    assert task["chunk_path"] == "/tmp/chunk_001.mbox"


def test_claim_task(test_pool):
    create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_001.mbox")
    claimed = claim_task(test_pool, phase="parse", worker_id="worker-1")
    assert claimed is not None
    assert claimed["status"] == "in_progress"
    assert claimed["worker_id"] == "worker-1"


def test_claim_task_skip_locked(test_pool):
    create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_001.mbox")
    create_task(test_pool, phase="parse", chunk_path="/tmp/chunk_002.mbox")
    t1 = claim_task(test_pool, phase="parse", worker_id="w1")
    t2 = claim_task(test_pool, phase="parse", worker_id="w2")
    assert t1["id"] != t2["id"]


def test_claim_task_returns_none_when_empty(test_pool):
    claimed = claim_task(test_pool, phase="parse", worker_id="w1")
    assert claimed is None


def test_complete_task(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    complete_task(
        test_pool,
        task["id"],
        messages_total=100,
        messages_inserted=95,
        messages_skipped=5,
        attachments_extracted=10,
    )
    status = get_phase_status(test_pool, "parse")
    assert status["completed"] == 1


def test_fail_task(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    fail_task(test_pool, task["id"], error="something broke")
    status = get_phase_status(test_pool, "parse")
    assert status["failed"] == 1


def test_reset_failed_tasks(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    fail_task(test_pool, task["id"], error="oops")
    count = reset_failed_tasks(test_pool, phase="parse", max_retries=3)
    assert count == 1
    status = get_phase_status(test_pool, "parse")
    assert status["pending"] == 1
    assert status["failed"] == 0


def test_reset_failed_tasks_skips_permanently_failed(test_pool):
    task = create_task(test_pool, phase="parse", chunk_path="/tmp/chunk.mbox")
    claim_task(test_pool, phase="parse", worker_id="w1")
    for _ in range(3):
        fail_task(test_pool, task["id"], error="oops")
    count = reset_failed_tasks(test_pool, phase="parse", max_retries=3)
    assert count == 0
