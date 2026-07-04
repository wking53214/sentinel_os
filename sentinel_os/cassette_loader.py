"""
Cassette Loader - Dynamically load and manage cassettes

Enables boom box to work with any cassette without code changes
"""

import importlib.util
import sys
from pathlib import Path
from typing import Dict, Optional
from cassette_interface import Cassette, CassetteRegistry

class CassetteLoader:
    """Load cassettes from files"""
    
    def __init__(self, cassette_dir: str = "./cassettes"):
        self.cassette_dir = Path(cassette_dir)
        self.registry = CassetteRegistry()
    
    def load_cassette(self, cassette_name: str) -> Cassette:
        """Load a single cassette by name"""
        
        module_name = f"{cassette_name}_cassette"
        class_name = "".join(word.capitalize() for word in cassette_name.split("_")) + "Cassette"
        
        try:
            # Dynamic import
            spec = importlib.util.spec_from_file_location(
                module_name,
                self.cassette_dir / f"{module_name}.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Get class and instantiate
            cassette_class = getattr(module, class_name)
            cassette = cassette_class()
            
            return cassette
        except Exception as e:
            raise ImportError(f"Failed to load cassette {cassette_name}: {e}")
    
    def load_all_cassettes(self) -> CassetteRegistry:
        """Auto-discover and load all cassettes"""
        
        cassette_files = self.cassette_dir.glob("*_cassette.py")
        
        for cassette_file in cassette_files:
            cassette_name = cassette_file.stem.replace("_cassette", "")
            
            try:
                cassette = self.load_cassette(cassette_name)
                self.registry.register(cassette)
                print(f"✓ Loaded cassette: {cassette_name}")
            except Exception as e:
                print(f"✗ Failed to load cassette {cassette_name}: {e}")
        
        return self.registry
    
    def get_cassette_for_domain(self, domain: str) -> Cassette:
        """Get cassette for specific domain"""
        return self.registry.get(domain)
    
    def list_available(self) -> Dict:
        """List all available cassettes"""
        return self.registry.list_all()

# Example usage
if __name__ == "__main__":
    loader = CassetteLoader()
    registry = loader.load_all_cassettes()
    
    print("\nAvailable cassettes:")
    for cassette_info in registry.list_all().items():
        print(f"  - {cassette_info[0]}: {cassette_info[1].description}")
    
    # Test loading specific cassette
    ivr = loader.get_cassette_for_domain("ivr")
    banking = loader.get_cassette_for_domain("banking")
    
    print(f"\nIVR queues: {list(ivr.get_queue_definitions().keys())}")
    print(f"Banking queues: {list(banking.get_queue_definitions().keys())}")
