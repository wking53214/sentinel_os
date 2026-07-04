"""
GALLM - Governor After the Large Language Models

Coordinates multi-AI workflow across Claude, Gemini, ChatGPT, Copilot, Perplexity
Routes tasks to right AI, enforces governance, maintains audit trail
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum
import json
import hashlib

class AIRole(Enum):
    CLAUDE = "claude"  # Home base, final decisions, governance
    GEMINI = "gemini"  # Strategy, reasoning, architecture
    CHATGPT = "chatgpt"  # Validation, code scrub, third audit
    COPILOT = "copilot"  # Brainstorm, creative (inconsistent)
    PERPLEXITY = "perplexity"  # Code audit, flaw finding

class TaskType(Enum):
    ARCHITECTURE = "architecture"  # Route to Gemini
    CODE_GENERATION = "code_generation"  # Route to Claude
    CODE_VALIDATION = "code_validation"  # Route to ChatGPT
    CODE_AUDIT = "code_audit"  # Route to Perplexity
    BRAINSTORM = "brainstorm"  # Route to Copilot
    GOVERNANCE_DECISION = "governance_decision"  # Route to Claude
    FINAL_REVIEW = "final_review"  # Route to Claude

@dataclass
class WorkItem:
    task_type: TaskType
    description: str
    context: Dict
    assigned_ai: Optional[AIRole] = None
    result: Optional[str] = None
    audit_trail: List[str] = None
    
    def __post_init__(self):
        if self.audit_trail is None:
            self.audit_trail = []

@dataclass
class AICapability:
    role: AIRole
    strengths: List[str]
    weaknesses: List[str]
    task_types: List[TaskType]
    token_limit: int
    cost_per_1k: float

class GALLMRouter:
    """Routes work to appropriate AI"""
    
    CAPABILITIES = {
        AIRole.CLAUDE: AICapability(
            role=AIRole.CLAUDE,
            strengths=["governance", "safety-critical", "final decisions", "code generation"],
            weaknesses=["slow reasoning", "limited search"],
            task_types=[TaskType.CODE_GENERATION, TaskType.GOVERNANCE_DECISION, TaskType.FINAL_REVIEW],
            token_limit=200000,
            cost_per_1k=0.003
        ),
        AIRole.GEMINI: AICapability(
            role=AIRole.GEMINI,
            strengths=["reasoning", "architecture", "strategy", "multimodal"],
            weaknesses=["inconsistent", "hallucination"],
            task_types=[TaskType.ARCHITECTURE, TaskType.BRAINSTORM],
            token_limit=1000000,
            cost_per_1k=0.00075
        ),
        AIRole.CHATGPT: AICapability(
            role=AIRole.CHATGPT,
            strengths=["code quality", "validation", "scrubbing"],
            weaknesses=["not latest", "slower"],
            task_types=[TaskType.CODE_VALIDATION],
            token_limit=128000,
            cost_per_1k=0.0015
        ),
        AIRole.COPILOT: AICapability(
            role=AIRole.COPILOT,
            strengths=["brainstorm", "creative", "fast"],
            weaknesses=["inconsistent grounding", "technical drift"],
            task_types=[TaskType.BRAINSTORM],
            token_limit=4096,
            cost_per_1k=0.0
        ),
        AIRole.PERPLEXITY: AICapability(
            role=AIRole.PERPLEXITY,
            strengths=["code audit", "flaw finding", "search-backed"],
            weaknesses=["limited reasoning"],
            task_types=[TaskType.CODE_AUDIT],
            token_limit=128000,
            cost_per_1k=0.001
        ),
    }
    
    def assign_task(self, work_item: WorkItem) -> WorkItem:
        """Route work item to appropriate AI"""
        
        # Governance decisions always go to Claude
        if work_item.task_type == TaskType.GOVERNANCE_DECISION:
            work_item.assigned_ai = AIRole.CLAUDE
            return work_item
        
        # Find matching AI
        for role, capability in self.CAPABILITIES.items():
            if work_item.task_type in capability.task_types:
                work_item.assigned_ai = role
                work_item.audit_trail.append(f"Routed to {role.value}")
                return work_item
        
        # Default to Claude
        work_item.assigned_ai = AIRole.CLAUDE
        return work_item

class GALLMExecutor:
    """Simulates AI responses (for testing)"""
    
    def execute(self, work_item: WorkItem) -> str:
        """Simulate AI execution"""
        
        ai = work_item.assigned_ai
        task = work_item.task_type
        
        # Simulate different AI behaviors
        responses = {
            (AIRole.CLAUDE, TaskType.CODE_GENERATION): "✓ Generated governance-safe code",
            (AIRole.CLAUDE, TaskType.GOVERNANCE_DECISION): "✓ Governance decision: APPROVED with audit logging",
            (AIRole.GEMINI, TaskType.ARCHITECTURE): "✓ Proposed architecture: modular, composable",
            (AIRole.CHATGPT, TaskType.CODE_VALIDATION): "✓ Code validated: no major issues",
            (AIRole.COPILOT, TaskType.BRAINSTORM): "✓ Brainstorm ideas: 5 options generated",
            (AIRole.PERPLEXITY, TaskType.CODE_AUDIT): "✓ Code audit: 2 potential issues found",
        }
        
        response = responses.get((ai, task), "✓ Task executed")
        work_item.result = response
        work_item.audit_trail.append(f"Executed: {response}")
        return response

class GALLMAudit:
    """Tamper-evident audit trail for multi-AI decisions"""
    
    def __init__(self):
        self.decisions = []
    
    def record_decision(self, work_item: WorkItem) -> str:
        """Record decision with hash chain"""
        
        audit_entry = {
            "task_type": work_item.task_type.value,
            "assigned_ai": work_item.assigned_ai.value if work_item.assigned_ai else None,
            "result": work_item.result,
            "trail_length": len(work_item.audit_trail),
        }
        
        # Hash previous entries for chain
        prev_hash = self.decisions[-1]["hash"] if self.decisions else "genesis"
        
        entry_str = json.dumps(audit_entry, sort_keys=True)
        current_hash = hashlib.sha256(f"{prev_hash}{entry_str}".encode()).hexdigest()
        
        audit_entry["hash"] = current_hash
        self.decisions.append(audit_entry)
        
        return current_hash
    
    def verify_chain(self) -> bool:
        """Verify hash chain integrity"""
        
        if not self.decisions:
            return True
        
        prev_hash = "genesis"
        for entry in self.decisions:
            recorded_hash = entry["hash"]
            
            # Recompute hash (excluding hash itself)
            verify_entry = {k: v for k, v in entry.items() if k != "hash"}
            entry_str = json.dumps(verify_entry, sort_keys=True)
            computed_hash = hashlib.sha256(f"{prev_hash}{entry_str}".encode()).hexdigest()
            
            if computed_hash != recorded_hash:
                return False
            
            prev_hash = recorded_hash
        
        return True

def orchestrate_iceberg_task(task_type: TaskType, description: str, context: Dict) -> Dict:
    """Complete orchestration: route → execute → audit"""
    
    # 1. Create work item
    work = WorkItem(task_type=task_type, description=description, context=context)
    
    # 2. Route
    router = GALLMRouter()
    work = router.assign_task(work)
    
    # 3. Execute
    executor = GALLMExecutor()
    executor.execute(work)
    
    # 4. Audit
    audit = GALLMAudit()
    audit_hash = audit.record_decision(work)
    audit.verify_chain()
    
    return {
        "task_type": work.task_type.value,
        "assigned_ai": work.assigned_ai.value,
        "result": work.result,
        "audit_hash": audit_hash,
        "chain_valid": audit.verify_chain()
    }
