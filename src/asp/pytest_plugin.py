"""pytest plugin: find out which AWS permissions your test suite needs.

    pytest --asp                     write the policy to asp-policy.json (see --asp-output)
    pytest --asp-check role.json     fail if the code needs actions role.json does not allow

Only AWS calls made inside the tests are recorded. Setup done in fixtures (for example
creating fake buckets with moto) is not, so it does not end up in the policy.
"""

from __future__ import annotations

import pytest

from asp.recorder import Recorder


def pytest_addoption(parser):
    group = parser.getgroup("asp", "least-privilege AWS policy from your tests")
    group.addoption("--asp", action="store_true", help="record AWS calls made by the tests")
    group.addoption(
        "--asp-output",
        default="asp-policy.json",
        help="where to write the policy (default: asp-policy.json)",
    )
    group.addoption(
        "--asp-check",
        metavar="POLICY",
        help="fail if the tests need actions this policy file does not allow",
    )


def pytest_configure(config):
    if config.getoption("--asp") or config.getoption("--asp-check"):
        config._asp_recorder = Recorder()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    recorder = getattr(item.config, "_asp_recorder", None)
    if recorder is None:
        yield
        return
    with recorder:  # record only while the test body runs
        yield


def pytest_sessionfinish(session):
    config = session.config
    recorder = getattr(config, "_asp_recorder", None)
    if recorder is None:
        return
    if config.getoption("--asp"):
        recorder.save(config.getoption("--asp-output"))
    policy_file = config.getoption("--asp-check")
    if policy_file:
        config._asp_result = recorder.check(policy_file)
        if not config._asp_result.ok and session.exitstatus == 0:
            session.exitstatus = 1


def pytest_terminal_summary(terminalreporter, config):
    recorder = getattr(config, "_asp_recorder", None)
    if recorder is None:
        return
    tr = terminalreporter
    tr.section("asp: AWS permissions used by the tests")
    tr.write_line(recorder.explain())
    if config.getoption("--asp"):
        tr.write_line(f"policy written to {config.getoption('--asp-output')}")
    result = getattr(config, "_asp_result", None)
    if result is not None:
        tr.write_line("")
        tr.write_line(result.summary())
        if not result.ok:
            tr.write_line("FAILED: the code needs permissions the policy does not allow", red=True)
