import sys
import os

# Add parent directory to path so tests can import modules
parent = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, parent)

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
