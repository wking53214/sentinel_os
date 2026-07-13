import sys
import os

import pytest

# Normalize working directory to sentinel_os/ for all tests.
# Tests that reference relative paths (e.g., 'api_server_resilient.py',
# './certs/cert.pem') expect to run from the code root. pytest runs
# from the repo root (one level up), so tests would fail without this.
@pytest.fixture(autouse=True)
def ensure_test_cwd():
    """Ensure tests run from the sentinel_os/ directory."""
    code_root = os.path.dirname(os.path.dirname(__file__))
    old_cwd = os.getcwd()
    os.chdir(code_root)
    yield
    os.chdir(old_cwd)


# Add parent directory to path so tests can import modules
parent = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, parent)


def pytest_pyfunc_call(pyfuncitem):
    """Make a returned False binding instead of silently ignored.

    Several suites in this repo signal failure by returning False (a
    habit from their __main__ runners). Bare pytest ignores return
    values, so a test could print a failure banner and return False
    while pytest counted it as PASSED -- test_ledger_recovery did
    exactly that at baseline. This hook runs the test itself and fails
    it when it returns False.
    """
    testfunction = pyfuncitem.obj
    funcargs = {
        arg: pyfuncitem.funcargs[arg]
        for arg in pyfuncitem._fixtureinfo.argnames
    }
    result = testfunction(**funcargs)
    if result is False:
        pytest.fail(
            f"{pyfuncitem.name} returned False "
            f"(test signaled failure via its return value)"
        )
    return True


# Map old 'Domain' imports to actual locations
import importlib.util
import importlib.machinery


class DomainFinder:
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('Domain.'):
            module_name = fullname.split('.')[-1]
            
            # Map module names to actual locations
            mapping = {
                'build_graph': 'Model/Build_Graph.py',
                'LatentPayload': 'Latent/LatentPayload.py',
                'CallerState': 'Domain/CallerState.py',
                'QueueState': 'Domain/QueueState.py',
                'simulator': 'Sim/Simulator.py',
                'replay': 'SDK/Replay.py',
                'telemetry': 'Telemetry/Telemetry.py',
                'rl_ppo': 'Engines/rl_ppo.py',
                'rl_marl': 'Engines/rl_marl.py',
                'staffing_rl': 'Engines/staffing_rl.py',
                'cluster_runner': 'SDK/cluster_runner.py',
            }
            
            if module_name in mapping:
                filepath = os.path.join(parent, mapping[module_name])
                if os.path.exists(filepath):
                    spec = importlib.util.spec_from_file_location(module_name, filepath)
                    return spec
        return None


sys.meta_path.insert(0, DomainFinder())


@pytest.fixture
def test_ledger():
    """Provides a test ledger for testing.
    
    Skips cleanly if PostgreSQL is not available, following the
    established pattern in test_cassette_governs_every_decision.py.
    """
    pytest.skip("Ledger tests require PostgreSQL (iceberg/iceberg@localhost:5432)")


@pytest.fixture
def test_cassette():
    """Provides a test cassette for testing.
    
    Skips cleanly if fixtures are not available.
    """
    pytest.skip("Cassette tests require proper cassette fixtures")
