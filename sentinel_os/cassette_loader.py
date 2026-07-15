"""
Cassette Loader - Dynamically load and manage cassettes

Enables boom box to work with any cassette without code changes
"""

import importlib.util
from pathlib import Path
from typing import Dict
from cassette_interface import Cassette, CassetteRegistry
from cassette_schema import CassetteValidationError, validate_cassette

class CassetteLoader:
    """Load cassettes from files"""
    
    def __init__(self, cassette_dir: str = "./cassettes"):
        # Try the provided path first
        target_path = Path(cassette_dir)
        
        # If it doesn't exist, check relative to this loader file's location
        if not target_path.exists():
            fallback_path = Path(__file__).parent / "cassettes"
            if fallback_path.exists():
                target_path = fallback_path
                
        self.cassette_dir = target_path
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
            class_obj = getattr(module, class_name)
            cassette = class_obj()
        except Exception as e:
            raise ImportError(f"Failed to load cassette {cassette_name}: {e}")

        # Fail-loud schema validation on the load path itself. A
        # cassette that cannot state its governance contract does not
        # load -- there is no partial load and no fallback default.
        validate_cassette(cassette)

        return cassette
    
    def load_all_cassettes(self, fail_on_invalid: bool = True) -> CassetteRegistry:
        """Auto-discover and load all cassettes.

        fail_on_invalid=True (the default) is the ONLY production
        posture: the first invalid cassette raises and nothing runs.
        Auto-discovery that silently skips a broken cassette turns a
        policy failure into a print statement -- the system would keep
        serving calls under whatever subset happened to load.

        fail_on_invalid=False is for debug/admin tooling only (cassette
        inventory, migration triage), where seeing the survivors next
        to the skip warnings is the point.
        """

        cassette_files = self.cassette_dir.glob("*_cassette.py")

        for cassette_file in cassette_files:
            cassette_name = cassette_file.stem.replace("_cassette", "")

            try:
                cassette = self.load_cassette(cassette_name)
                self.registry.register(cassette)
                print(f"✓ Loaded cassette: {cassette_name}")
            except CassetteValidationError:
                if fail_on_invalid:
                    raise
                print(f"⚠ Skipped invalid cassette: {cassette_name}")
            except Exception as e:
                if fail_on_invalid:
                    raise
                print(f"⚠ Skipped unloadable cassette: {cassette_name} ({e})")

        return self.registry

    @classmethod
    def production_mode(cls, domain: str, cassette_dir: str = "./cassettes") -> Cassette:
        """Production entry point: load EXACTLY the named domain's
        cassette -- explicit selection, full fail-loud validation, and
        NO directory glob. A broken or malicious neighbor file in the
        cassette directory cannot break, delay, or hijack this load,
        because it is never opened.
        """
        loader = cls(cassette_dir)
        cassette = loader.load_cassette(domain)
        loader.registry.register(cassette)
        return cassette
    
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
