import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gallm_coordinator import (
    GALLMRouter, GALLMExecutor, GALLMAudit, WorkItem, TaskType, AIRole,
    orchestrate_iceberg_task
)

def test_gallm_routing():
    print("\n[TEST 1] GALLM routing: tasks to appropriate AIs")
    router = GALLMRouter()
    
    # Architecture → Gemini
    work1 = WorkItem(TaskType.ARCHITECTURE, "Design new layer", {})
    work1 = router.assign_task(work1)
    assert work1.assigned_ai == AIRole.GEMINI, f"Should route to Gemini, got {work1.assigned_ai}"
    
    # Code generation → Claude
    work2 = WorkItem(TaskType.CODE_GENERATION, "Generate router", {})
    work2 = router.assign_task(work2)
    assert work2.assigned_ai == AIRole.CLAUDE, f"Should route to Claude, got {work2.assigned_ai}"
    
    # Code audit → Perplexity
    work3 = WorkItem(TaskType.CODE_AUDIT, "Find flaws", {})
    work3 = router.assign_task(work3)
    assert work3.assigned_ai == AIRole.PERPLEXITY, f"Should route to Perplexity, got {work3.assigned_ai}"
    
    # Governance decision → Claude (always)
    work4 = WorkItem(TaskType.GOVERNANCE_DECISION, "Approve drift policy", {})
    work4 = router.assign_task(work4)
    assert work4.assigned_ai == AIRole.CLAUDE, f"Should route to Claude, got {work4.assigned_ai}"
    
    print(f"  ✓ PASSED - Routed 4 tasks to correct AIs")
    return True

def test_gallm_execution():
    print("\n[TEST 2] GALLM execution: simulate AI responses")
    executor = GALLMExecutor()
    
    work1 = WorkItem(TaskType.CODE_GENERATION, "Generate router", {})
    work1.assigned_ai = AIRole.CLAUDE
    result1 = executor.execute(work1)
    assert "✓" in result1, "Should succeed"
    assert work1.result is not None
    
    work2 = WorkItem(TaskType.CODE_AUDIT, "Audit code", {})
    work2.assigned_ai = AIRole.PERPLEXITY
    result2 = executor.execute(work2)
    assert "✓" in result2, "Should succeed"
    
    print(f"  ✓ PASSED - Executed 2 tasks with AI responses")
    return True

def test_gallm_audit():
    print("\n[TEST 3] GALLM audit: tamper-evident trail")
    audit = GALLMAudit()
    
    # Record 3 decisions
    for i in range(3):
        work = WorkItem(TaskType.GOVERNANCE_DECISION, f"Decision {i}", {})
        work.assigned_ai = AIRole.CLAUDE
        work.result = f"Result {i}"
        audit.record_decision(work)
    
    assert len(audit.decisions) == 3, "Should have 3 decisions"
    
    # Verify chain
    chain_valid = audit.verify_chain()
    assert chain_valid, "Chain should be valid"
    
    # Verify hash is 64 chars
    for decision in audit.decisions:
        assert len(decision["hash"]) == 64, "Hash should be SHA256"
    
    print(f"  ✓ PASSED - Recorded 3 decisions with valid hash chain")
    return True

def test_gallm_end_to_end():
    print("\n[TEST 4] GALLM end-to-end orchestration")
    
    tasks = [
        (TaskType.ARCHITECTURE, "Design governance layer"),
        (TaskType.CODE_GENERATION, "Generate RL trainer"),
        (TaskType.CODE_VALIDATION, "Validate code"),
        (TaskType.CODE_AUDIT, "Audit for flaws"),
        (TaskType.GOVERNANCE_DECISION, "Approve system"),
    ]
    
    results = []
    for task_type, desc in tasks:
        result = orchestrate_iceberg_task(task_type, desc, {})
        results.append(result)
        assert result["chain_valid"], f"Chain should be valid for {task_type}"
        assert result["result"] is not None, f"Should have result for {task_type}"
    
    print(f"  ✓ PASSED - Orchestrated {len(results)} Iceberg tasks end-to-end")
    for r in results:
        print(f"             {r['task_type']:20s} → {r['assigned_ai']:10s}")
    return True

def main():
    print("\n" + "="*70)
    print("GALLM MULTI-AI ORCHESTRATION TESTS")
    print("="*70)
    
    tests = [
        ("GALLM routing", test_gallm_routing),
        ("GALLM execution", test_gallm_execution),
        ("GALLM audit", test_gallm_audit),
        ("GALLM end-to-end", test_gallm_end_to_end),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            results.append(test_fn())
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    passed = sum(results)
    total = len(results)
    
    print("\n" + "="*70)
    print(f"GALLM RESULTS: {passed}/{total} tests passed")
    print("="*70 + "\n")
    
    return all(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
