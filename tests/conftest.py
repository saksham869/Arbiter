"""
Test configuration — resets shared state between test modules.
Prevents velocity counter and kill switch leaking between test files.
"""
import pytest


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset kill switch and velocity tracker between every test."""
    import control.app as app_module
    # Reset kill switch
    app_module._kill_switch_active = False
    yield
    # Teardown — reset again after test
    app_module._kill_switch_active = False


@pytest.fixture(autouse=True)
def reset_velocity():
    """Reset velocity tracker between tests to prevent rate-limit leakage."""
    try:
        from control.app import _velocity_tracker
        _velocity_tracker.reset()
    except Exception:
        pass
    yield
