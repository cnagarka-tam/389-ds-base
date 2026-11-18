# --- BEGIN COPYRIGHT BLOCK ---
# Copyright (C) 2026 Red Hat, Inc.
# All rights reserved.
#
# License: GPL (version 3 or any later version).
# See LICENSE for details.
# --- END COPYRIGHT BLOCK ---
#

from argparse import Namespace
from unittest.mock import Mock, call

import pytest
from lib389.cli_conf import backend

pytestmark = pytest.mark.tier1


@pytest.mark.parametrize("exit_code, error", [
    (0, None),
    (None, "has an unknown outcome (exit code unavailable)"),
    (1, "failed with exit code 1"),
])
def test_backend_compact_reports_task_dn(monkeypatch, exit_code, error):
    """Check task reporting when compact-db waits for the server.

    :id: 11d6cfbd-2533-40fe-8882-e512d9671048
    :setup: Mocked compaction task
    :steps:
        1. Submit a changelog compaction task with a wait timeout.
        2. Simulate successful completion, no exit code, or failure.
    :expectedresults:
        1. The task DN is logged before waiting.
        2. The completion message or error identifies the same task.
    """
    events = Mock()
    task = events.task
    task.dn = "cn=example,cn=compact db,cn=tasks,cn=config"
    task.get_exit_code.return_value = exit_code
    monkeypatch.setattr(backend, "DBCompactTask", Mock(return_value=task))

    args = Namespace(only_changelog=True, timeout=5)
    if error is None:
        backend.backend_compact(None, None, events.log, args)
    else:
        with pytest.raises(ValueError) as exc:
            backend.backend_compact(None, None, events.log, args)
        assert task.dn in str(exc.value)
        assert error in str(exc.value)

    expected = [
        call.task.create(properties={"justChangelog": "yes"}),
        call.log.info(f"Started database compaction task {task.dn}"),
        call.task.wait(timeout=5),
        call.task.get_exit_code(),
    ]
    if error is None:
        expected.append(call.log.info(f"Database compaction task {task.dn} completed successfully"))
    assert events.mock_calls == expected


@pytest.mark.parametrize("monitor_error", [False, True])
@pytest.mark.parametrize("exit_code", [0, None])
def test_backend_compact_reports_disk_snapshots(monkeypatch, monitor_error, exit_code):
    """Check disk snapshots around a compaction task.

    :id: a0b24724-ac3c-4d24-88e4-672231229711
    :setup: Mocked compaction and LDAP disk monitor
    :steps:
        1. Run compaction with disk reporting enabled.
        2. Repeat with an LDAP error and with an unavailable task exit code.
    :expectedresults:
        1. The CLI samples before submission and after waiting.
        2. The CLI preserves the task outcome and labels an incomplete wait.
    """
    events = Mock()
    task = events.task
    task.dn = "cn=example,cn=compact db,cn=tasks,cn=config"
    task.get_exit_code.return_value = exit_code
    disks = events.monitor.get_disks
    disks.return_value = ['partition="/var" available="1024"']
    if monitor_error:
        disks.side_effect = backend.LDAPError("monitor unavailable")

    monkeypatch.setattr(backend, "DBCompactTask", Mock(return_value=task))
    monkeypatch.setattr(
        backend, "MonitorDiskSpace", Mock(return_value=events.monitor)
    )

    args = Namespace(only_changelog=True, timeout=5, report_disk_space=True)
    if exit_code is None:
        with pytest.raises(ValueError, match="unknown outcome"):
            backend.backend_compact(None, None, events.log, args)
    else:
        backend.backend_compact(None, None, events.log, args)

    operations = [event for event in events.mock_calls if event[0] != "log.info"]
    assert operations == [
        call.monitor.get_disks(),
        call.task.create(properties={"justChangelog": "yes"}),
        call.task.wait(timeout=5),
        call.task.get_exit_code(),
        call.monitor.get_disks(),
    ]
    messages = [entry.args[0] for entry in events.log.info.call_args_list]
    expected = "Disk-space data unavailable" if monitor_error else 'available="1024"'
    assert any(f"{task.dn} before submission" in msg and expected in msg for msg in messages)
    phase = "wait ended" if exit_code is None else "task completed"
    assert any(f"{task.dn} {phase}" in msg and expected in msg for msg in messages)
    if exit_code == 0:
        assert messages[-1] == f"Database compaction task {task.dn} completed successfully"
