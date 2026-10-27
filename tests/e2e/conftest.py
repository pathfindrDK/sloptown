import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Expose each test's call-phase report as item.rep_call (used by test_e2e._chain)."""
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item.rep_call = rep
