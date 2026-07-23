"""
Cassette Loader - Dynamically load and manage cassettes

Enables boom box to work with any cassette without code changes
"""

import importlib.util
import sys
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
            module = self._load_module(module_name)
            
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

    def _load_module(self, module_name: str):
        """Load `module_name` from self.cassette_dir.

        When self.cassette_dir IS the real `cassettes` package shipped with
        this repo, use a normal `importlib.import_module("cassettes.X")` --
        the identical import every other caller in the codebase uses (e.g.
        `from cassettes.banking_cassette import BankingCassette`). This
        matters beyond style: it's what makes the resulting class's
        __module__ read "cassettes.X" instead of a bare "X", which is what
        cassette_forensics.compute_cassette_code_hash labels its hash with.
        A cassette loaded through THIS loader must hash identically to the
        same class loaded through a plain import, or the two code paths
        silently disagree about what the "same" cassette's code hash is --
        which is exactly what used to happen here (see the fix note below).

        Falls back to the low-level spec_from_file_location + exec_module
        recipe only for a genuinely custom/external cassette_dir that isn't
        an importable package member -- the loader's whole point is to also
        support loading from outside the repo. That fallback still
        registers the module in sys.modules (set BEFORE exec_module, same
        as CPython's own import machinery, and needed for inspect.getmodule
        to resolve it at all -- without this, compute_cassette_code_hash
        silently fell back to an "UNAVAILABLE_CASSETTE_SOURCE" marker
        instead of the cassette's real source for every cassette loaded
        this way, and two different code hashes for the identical class --
        the real one from a direct import, the marker from here -- would
        collide the first time both got bound to the same cassette_version
        in the ledger, tripping bind_cassette_version's content-mismatch
        tripwire on a false positive).
        """
        real_package_dir = Path(__file__).parent / "cassettes"
        if self.cassette_dir.resolve() == real_package_dir.resolve():
            return importlib.import_module(f"cassettes.{module_name}")

        spec = importlib.util.spec_from_file_location(
            module_name,
            self.cassette_dir / f"{module_name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            # Don't leave a half-executed module registered under this
            # name -- a failed load should look like no load, not a
            # module future code could accidentally import.
            sys.modules.pop(module_name, None)
            raise
        return module
    
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
