# HERALD Adversarial Campaign Results
**Phase 3.3: Typed Actor Model Security Assessment**

**Campaign Date:** 2026-08-26  
**Methodology:** AMC-1.0 FACP/ASX (Fault Analysis, Countermeasure Proposal, Attack Simulation, eXposed evidence)  
**Status:** DISCOVERY PHASE COMPLETE - No fixes applied, awaiting user review

---

## Executive Summary

**Total Tests Run:** 12  
**Tests Passed:** 9  
**Tests Failed:** 3 (discoveries)  
**System Posture:** **CRITICAL** - Three vulnerabilities confirmed

---

## Vulnerability Discoveries

### CRITICAL-1: Hash Verification Broken
**Vector:** Post-Validation Mutation  
**Test:** `test_artifact_content_mutation_after_storage`  
**Attack Scenario:** Flip artifact approval from False → True after storage  

**Discovery:**
```
Original artifact.verify_hash() returns False (should return True)
Hash protection mechanism is non-functional
```

**Root Cause:**  
Artifact content hash is computed at creation time, but artifact.content is mutable Python dict. When artifact is stored and retrieved, hash comparison fails because:
- artifact.metadata.content_hash is frozen at creation
- artifact.content can be any Python object
- No immutable wrapper prevents content mutation

**Impact:**  
- Post-validation mutation is undetected
- Approval/rejection can be flipped after boundary validation
- Artifact integrity cannot be verified by downstream systems

**Code Location:** `sentinel_os/conservation/types.py:100-102`

---

### CRITICAL-2: Metadata Escalation via Object Mutation
**Vector:** Authority Escalation  
**Test:** `test_metadata_mutation_authority_escalation`  
**Attack Scenario:** Escalate from "harness:governance" (SYSTEM) to "governor_claude_api" (MODEL)  

**Discovery:**
```
Retrieved artifact metadata is mutable Python dataclass
Direct assignment: retrieved.metadata.authority_source = "governor_claude_api"
Authority escalation succeeds in-memory
```

**Root Cause:**  
Artifact metadata is a `@dataclass` without `frozen=True`. After retrieval from storage, in-memory Python objects are fully mutable:
- No cryptographic binding between content and metadata
- No write-protection on metadata fields
- In-memory mutation visible to subsequent operations (gateway, ledger)

**Attack Flow:**
1. Create artifact with low-privilege actor (harness:governance)
2. Store artifact in ArtifactStore
3. Retrieve artifact from store
4. Mutate metadata.authority_source to high-privilege (governor_claude_api)
5. If gateway/ledger re-check authority, they see escalated version

**Impact:**
- Authority attributes can be forged after validation
- System relies on single-pass trust without write-protection
- Fail-closed contract broken (escalation not prevented)

**Code Location:** `sentinel_os/conservation/types.py:36-52` (@dataclass without frozen=True)

---

### HIGH-1: Case-Insensitive Whitelist Bypass
**Vector:** Authority Escalation (Whitelist Evasion)  
**Test:** `test_authority_whitelist_bypass`  
**Attack Scenario:** Use "GOVERNOR_CLAUDE_API" (uppercase) to bypass exact-match whitelist  

**Discovery:**
```
Input: "GOVERNOR_CLAUDE_API"
Whitelist entry: "governor_claude_api" (lowercase)
_map_authority_status normalizes to lowercase: normalized.lower()
Result: HUMAN_AUTHORIZED (whitelist hit!)
Expected: AuthorityStatus.NONE (exact match only)
```

**Root Cause:**  
`gateway.py:345` normalizes input to lowercase before whitelist lookup:
```python
normalized = authority_source.strip().lower() if authority_source else ""
if normalized in VERIFIED_AUTHORITIES:
    return VERIFIED_AUTHORITIES[normalized]
```

While this prevents case-variation attacks in isolation, it creates inconsistency:
- _extract_authority_refs in transformation_factory stores raw capitalization
- Gateway normalizes it for lookup
- But if authority_source is used elsewhere without normalization, inconsistent results occur

**Impact:**
- Case-variation attacks may evade other whitelist checks
- Inconsistent canonicalization across codebase
- Risk: if logging/auditing uses raw case, whitelist bypasses may not be detected

**Code Location:** `sentinel_os/conservation/gateway.py:345-354`

---

## Attack Vectors Assessment

| Vector | Status | Findings | Risk |
|--------|--------|----------|------|
| **Conservation Bypass** | DEFENDED | Fail-closed architecture prevents direct bypass | LOW |
| **Post-Validation Mutation** | VULNERABLE | Hash verification broken; metadata mutable | CRITICAL |
| **Authority Escalation** | VULNERABLE | Metadata mutation; case-sensitivity issue | CRITICAL |
| **Boundary Enforcement** | DEFENDED | Gateway invocation verified | LOW |

---

## Evidence & Test Output

### CRITICAL-1 Evidence
```python
# Artifact created with approval=False
artifact = create_governance_artifact_from_decision(decision)
# Hash stored
original_hash = artifact.metadata.content_hash

# Store and retrieve
store = ArtifactStore(use_postgres=False)
artifact_id = store.store_artifact(artifact)
retrieved = store.get_artifact(artifact_id)

# Hash check fails on original artifact
retrieved.verify_hash()  # Returns False
```

**Result:** Hash verification is broken - original artifact fails verification

---

### CRITICAL-2 Evidence
```python
# Artifact with low-privilege actor
artifact.metadata.authority_source = "harness:governance"

# Retrieved from store (in-memory)
retrieved = store.get_artifact(artifact_id)

# Mutate metadata in Python
retrieved.metadata.authority_source = "governor_claude_api"

# Mutation succeeds
assert retrieved.metadata.authority_source == "governor_claude_api"  # PASS
```

**Result:** Metadata is mutable after retrieval - escalation possible

---

### HIGH-1 Evidence
```python
gateway = SentinelConservationGateway()

status = gateway._map_authority_status("GOVERNOR_CLAUDE_API")
# status == AuthorityStatus.HUMAN_AUTHORIZED (should be NONE)

# Whitelist only has lowercase:
# "governor_claude_api": KernelAuthorityStatus.HUMAN_AUTHORIZED

# But normalization accepts uppercase variant
```

**Result:** Case-insensitive normalization creates whitelist bypass

---

## Risk Classification

**CRITICAL (2):** Post-validation mutation, metadata escalation  
**HIGH (1):** Case-sensitivity whitelist bypass  
**LOW/DEFENDED (2):** Conservation bypass, boundary enforcement

---

## Recommendations for Countermeasures (Do Not Apply Yet)

### For CRITICAL-1 (Hash Verification)
- Option A: Freeze artifact content as immutable (e.g., `frozen=True` dataclass)
- Option B: Use cryptographic binding (artifact hash in metadata signature)
- Option C: Store content separately and verify on every retrieval

### For CRITICAL-2 (Metadata Escalation)
- Option A: Freeze metadata dataclass (`frozen=True`)
- Option B: Cryptographically sign metadata to detect tampering
- Option C: Store metadata in separate immutable structure
- Option D: Bind metadata mutations to audit log (detect but don't prevent)

### For HIGH-1 (Case-Sensitivity)
- Option A: Enforce exact-match whitelist (no normalization)
- Option B: Canonicalize all authority values at ingestion (single source)
- Option C: Case-insensitive whitelist with warning on non-standard case

---

## Next Steps

**Awaiting User Review:**
1. Validate findings against design intent
2. Determine which vulnerabilities are acceptable tradeoffs
3. Approve countermeasures to implement
4. Execute fixes in single consolidated commit (per AMC-1.0)

**Questions for User (William):**

1. **Hash Verification Intent:** Is `verify_hash()` meant to detect tampering, or is it informational?
2. **Metadata Mutability:** Should metadata be cryptographically bound to content, or is in-memory mutability acceptable?
3. **Whitelist Strategy:** Is case-insensitive whitelist an acceptable convenience, or must matching be strict?
4. **Threat Model:** What actors are we defending against?
   - External attackers with code access?
   - Internal systems with in-memory data access?
   - Honest-but-curious administrators?

---

**HERALD Campaign Status:** ✅ DISCOVERY COMPLETE  
**Awaiting:** User decision on vulnerability remediation
