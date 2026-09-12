"""
Requirement Analyzer - Understands user intent and requirements for self-improvement.

This module analyzes detected self-improvement triggers to understand:
- What the user wants to achieve
- Which modules/components are affected
- What changes are needed
- The scope and complexity of the request

It uses AI-powered analysis combined with code graph queries to build a
comprehensive understanding of the requirement.

Key Features:
- AI-powered requirement extraction
- Code graph-based impact analysis
- Module identification and dependency mapping
- Complexity estimation
- Feasibility assessment
"""

import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.ai import call_llm_text
from code_graph.engine import CodeGraphEngine


class RequirementType(Enum):
    """Types of requirements."""
    BUG_FIX = "bug_fix"              # Fix a specific bug
    FEATURE_ADD = "feature_add"      # Add new functionality
    OPTIMIZATION = "optimization"    # Improve performance
    REFACTORING = "refactoring"     # Reorganize code
    INTEGRATION = "integration"     # Integrate external code
    CONFIG_CHANGE = "config_change"  # Change configuration
    DEPENDENCY_UPDATE = "dependency_update"  # Update dependencies


class Complexity(Enum):
    """Complexity levels."""
    TRIVIAL = "trivial"      # < 1 hour, single file, low risk
    SIMPLE = "simple"        # < 4 hours, few files, low risk
    MODERATE = "moderate"    # < 1 day, multiple files, medium risk
    COMPLEX = "complex"      # < 1 week, many files, high risk
    VERY_COMPLEX = "very_complex"  # > 1 week, core changes, very high risk


@dataclass
class Requirement:
    """Represents a self-improvement requirement."""
    original_request: str
    requirement_type: RequirementType
    description: str
    affected_modules: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    complexity: Complexity = Complexity.MODERATE
    estimated_hours: float = 0.0
    feasibility_score: float = 0.0  # 0.0 to 1.0
    risks: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "original_request": self.original_request,
            "requirement_type": self.requirement_type.value,
            "description": self.description,
            "affected_modules": self.affected_modules,
            "affected_files": self.affected_files,
            "dependencies": self.dependencies,
            "complexity": self.complexity.value,
            "estimated_hours": self.estimated_hours,
            "feasibility_score": self.feasibility_score,
            "risks": self.risks,
            "assumptions": self.assumptions,
            "success_criteria": self.success_criteria,
        }


class RequirementAnalyzer:
    """
    Analyzes self-improvement requirements.
    
    Uses AI to understand user intent and code graph to analyze impact.
    """
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the requirement analyzer.
        
        Args:
            project_root: Path to project root (auto-detected if None)
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        
        self.project_root = project_root
        self.code_graph = CodeGraphEngine(str(project_root))
        
        # Module mapping for common JARVIS components
        self.module_map = {
            "main": ["main.py"],
            "actions": ["actions/"],
            "memory": ["memory/"],
            "dashboard": ["dashboard/"],
            "self_engineer": ["self_engineer/"],
            "code_graph": ["code_graph/"],
            "research": ["research/"],
            "core": ["core/"],
            "ui": ["ui/"],
            "config": ["config/"],
        }
    
    def analyze(
        self, 
        request: str, 
        trigger_type: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Requirement:
        """
        Analyze a self-improvement request.
        
        Args:
            request: Original user request
            trigger_type: Type of trigger detected
            context: Additional context (e.g., current file, recent actions)
            
        Returns:
            Requirement object with full analysis
        """
        print(f"[RequirementAnalyzer] Analyzing: {request[:100]}...")
        
        # Step 1: AI-powered requirement extraction
        req_type, description = self._extract_requirement(request, trigger_type)
        
        # Step 2: Identify affected modules
        affected_modules, affected_files = self._identify_affected_modules(
            request, description, context
        )
        
        # Step 3: Analyze dependencies
        dependencies = self._analyze_dependencies(affected_files)
        
        # Step 4: Estimate complexity
        complexity, estimated_hours = self._estimate_complexity(
            req_type, affected_files, description
        )
        
        # Step 5: Assess feasibility
        feasibility_score = self._assess_feasibility(
            req_type, complexity, affected_files
        )
        
        # Step 6: Identify risks
        risks = self._identify_risks(req_type, complexity, affected_files)
        
        # Step 7: Extract assumptions
        assumptions = self._extract_assumptions(description, context)
        
        # Step 8: Define success criteria
        success_criteria = self._define_success_criteria(req_type, description)
        
        return Requirement(
            original_request=request,
            requirement_type=req_type,
            description=description,
            affected_modules=affected_modules,
            affected_files=affected_files,
            dependencies=dependencies,
            complexity=complexity,
            estimated_hours=estimated_hours,
            feasibility_score=feasibility_score,
            risks=risks,
            assumptions=assumptions,
            success_criteria=success_criteria,
        )
    
    def _extract_requirement(
        self, 
        request: str, 
        trigger_type: str
    ) -> tuple[RequirementType, str]:
        """
        Use AI to extract the requirement type and description.
        
        Args:
            request: Original user request
            trigger_type: Detected trigger type
            
        Returns:
            Tuple of (requirement_type, description)
        """
        try:
            prompt = f"""Analyze this self-improvement request for an AI assistant.

Request: "{request}"
Trigger Type: {trigger_type}

Determine:
1. What type of requirement is this? (bug_fix, feature_add, optimization, refactoring, integration, config_change, dependency_update)
2. What specifically needs to be done? Provide a clear, detailed description.

Answer in this format:
TYPE: [type]
DESCRIPTION: [detailed description]"""
            
            response = call_llm_text(
                prompt,
                timeout=60,
                task="requirement_extraction"
            )
            
            # Parse response
            req_type = RequirementType.MODERATE  # default
            description = request  # default
            
            for line in response.split('\n'):
                line = line.strip()
                if line.startswith('TYPE:'):
                    type_str = line.split(':', 1)[1].strip().lower()
                    type_map = {
                        "bug_fix": RequirementType.BUG_FIX,
                        "feature_add": RequirementType.FEATURE_ADD,
                        "optimization": RequirementType.OPTIMIZATION,
                        "refactoring": RequirementType.REFACTORING,
                        "integration": RequirementType.INTEGRATION,
                        "config_change": RequirementType.CONFIG_CHANGE,
                        "dependency_update": RequirementType.DEPENDENCY_UPDATE,
                    }
                    req_type = type_map.get(type_str, RequirementType.MODERATE)
                elif line.startswith('DESCRIPTION:'):
                    description = line.split(':', 1)[1].strip()
            
            return req_type, description
            
        except Exception as e:
            print(f"[RequirementAnalyzer] AI extraction failed: {e}")
            # Fallback to simple classification
            if "fix" in request.lower() or "bug" in request.lower():
                return RequirementType.BUG_FIX, request
            elif "add" in request.lower() or "feature" in request.lower():
                return RequirementType.FEATURE_ADD, request
            elif "optimize" in request.lower() or "faster" in request.lower():
                return RequirementType.OPTIMIZATION, request
            else:
                return RequirementType.MODERATE, request
    
    def _identify_affected_modules(
        self,
        request: str,
        description: str,
        context: Optional[Dict[str, Any]]
    ) -> tuple[List[str], List[str]]:
        """
        Identify which modules and files are affected by this requirement.
        
        Args:
            request: Original request
            description: Requirement description
            context: Additional context
            
        Returns:
            Tuple of (affected_modules, affected_files)
        """
        affected_modules = []
        affected_files = []
        
        # Check context first (e.g., if user is looking at a specific file)
        if context and "current_file" in context:
            current_file = context["current_file"]
            affected_files.append(current_file)
            
            # Determine module from file path
            for module, paths in self.module_map.items():
                for path in paths:
                    if path in current_file:
                        if module not in affected_modules:
                            affected_modules.append(module)
        
        # Use AI to identify modules from description
        try:
            prompt = f"""Which JARVIS modules are affected by this requirement?

Description: {description}

Common JARVIS modules:
- main: Main entry point (main.py)
- actions: Tool implementations (browser_control, computer_control, code_helper, etc.)
- memory: Memory management system
- dashboard: Remote dashboard server
- self_engineer: Self-improvement system
- code_graph: Code dependency analysis
- research: Research pipeline
- core: Core AI routing and utilities
- ui: User interface
- config: Configuration files

List the affected modules (comma-separated, or "none" if unclear):"""
            
            response = call_llm_text(
                prompt,
                timeout=30,
                task="module_identification"
            ).strip().lower()
            
            if response and response != "none":
                for module in response.split(','):
                    module = module.strip()
                    if module in self.module_map and module not in affected_modules:
                        affected_modules.append(module)
                        # Add module files
                        for path in self.module_map[module]:
                            if path.endswith('/'):
                                # It's a directory - add common files
                                affected_files.append(path)
                            else:
                                affected_files.append(path)
        
        except Exception as e:
            print(f"[RequirementAnalyzer] Module identification failed: {e}")
        
        # If no modules identified, assume it might affect core
        if not affected_modules:
            affected_modules.append("core")
        
        return affected_modules, list(set(affected_files))
    
    def _analyze_dependencies(self, affected_files: List[str]) -> List[str]:
        """
        Analyze dependencies of affected files.
        
        Args:
            affected_files: List of affected file paths
            
        Returns:
            List of dependent files/modules
        """
        dependencies = []
        
        try:
            for file_path in affected_files:
                # Skip directories
                if file_path.endswith('/'):
                    continue
                
                # Get dependents from code graph
                dependents = self.code_graph.get_dependents(file_path)
                if dependents:
                    dependencies.extend(dependents[:5])  # Limit to top 5
        except Exception as e:
            print(f"[RequirementAnalyzer] Dependency analysis failed: {e}")
        
        return list(set(dependencies))
    
    def _estimate_complexity(
        self,
        req_type: RequirementType,
        affected_files: List[str],
        description: str
    ) -> tuple[Complexity, float]:
        """
        Estimate the complexity of the requirement.
        
        Args:
            req_type: Type of requirement
            affected_files: List of affected files
            description: Requirement description
            
        Returns:
            Tuple of (complexity, estimated_hours)
        """
        # Base complexity by type
        complexity_map = {
            RequirementType.BUG_FIX: Complexity.SIMPLE,
            RequirementType.FEATURE_ADD: Complexity.MODERATE,
            RequirementType.OPTIMIZATION: Complexity.MODERATE,
            RequirementType.REFACTORING: Complexity.COMPLEX,
            RequirementType.INTEGRATION: Complexity.COMPLEX,
            RequirementType.CONFIG_CHANGE: Complexity.TRIVIAL,
            RequirementType.DEPENDENCY_UPDATE: Complexity.SIMPLE,
        }
        
        base_complexity = complexity_map.get(req_type, Complexity.MODERATE)
        
        # Adjust based on number of affected files
        file_count = len(affected_files)
        if file_count == 0:
            file_count = 1
        
        if file_count == 1:
            complexity = base_complexity
        elif file_count <= 3:
            complexity = self._increase_complexity(base_complexity, 1)
        elif file_count <= 10:
            complexity = self._increase_complexity(base_complexity, 2)
        else:
            complexity = Complexity.VERY_COMPLEX
        
        # Estimate hours based on complexity
        hours_map = {
            Complexity.TRIVIAL: 0.5,
            Complexity.SIMPLE: 2.0,
            Complexity.MODERATE: 6.0,
            Complexity.COMPLEX: 24.0,
            Complexity.VERY_COMPLEX: 40.0,
        }
        
        estimated_hours = hours_map.get(complexity, 6.0)
        
        # Adjust based on description length (more detail = more complex)
        if len(description) > 500:
            estimated_hours *= 1.5
        
        return complexity, estimated_hours
    
    def _increase_complexity(self, current: Complexity, steps: int) -> Complexity:
        """Increase complexity by given steps."""
        levels = [
            Complexity.TRIVIAL,
            Complexity.SIMPLE,
            Complexity.MODERATE,
            Complexity.COMPLEX,
            Complexity.VERY_COMPLEX,
        ]
        try:
            idx = levels.index(current)
            new_idx = min(idx + steps, len(levels) - 1)
            return levels[new_idx]
        except ValueError:
            return Complexity.MODERATE
    
    def _assess_feasibility(
        self,
        req_type: RequirementType,
        complexity: Complexity,
        affected_files: List[str]
    ) -> float:
        """
        Assess the feasibility of implementing this requirement.
        
        Args:
            req_type: Type of requirement
            complexity: Estimated complexity
            affected_files: Affected files
            
        Returns:
            Feasibility score (0.0 to 1.0)
        """
        # Base feasibility by complexity
        feasibility_map = {
            Complexity.TRIVIAL: 0.95,
            Complexity.SIMPLE: 0.85,
            Complexity.MODERATE: 0.70,
            Complexity.COMPLEX: 0.50,
            Complexity.VERY_COMPLEX: 0.30,
        }
        
        base_feasibility = feasibility_map.get(complexity, 0.70)
        
        # Adjust based on requirement type
        if req_type == RequirementType.BUG_FIX:
            base_feasibility += 0.10  # Bug fixes are usually feasible
        elif req_type == RequirementType.FEATURE_ADD:
            base_feasibility -= 0.10  # New features are harder
        elif req_type == RequirementType.INTEGRATION:
            base_feasibility -= 0.15  # Integration is complex
        
        # Adjust based on affected files (core files are riskier)
        core_files = ["main.py", "core/", "self_engineer/"]
        if any(cf in " ".join(affected_files) for cf in core_files):
            base_feasibility -= 0.15
        
        return max(0.0, min(1.0, base_feasibility))
    
    def _identify_risks(
        self,
        req_type: RequirementType,
        complexity: Complexity,
        affected_files: List[str]
    ) -> List[str]:
        """
        Identify potential risks for this requirement.
        
        Args:
            req_type: Requirement type
            complexity: Complexity level
            affected_files: Affected files
            
        Returns:
            List of risk descriptions
        """
        risks = []
        
        # Complexity-based risks
        if complexity in [Complexity.COMPLEX, Complexity.VERY_COMPLEX]:
            risks.append("High complexity may lead to unexpected side effects")
            risks.append("May require extensive testing")
        
        # Core file risks
        core_files = ["main.py", "core/", "self_engineer/"]
        if any(cf in " ".join(affected_files) for cf in core_files):
            risks.append("Modifying core files could break system stability")
            risks.append("May affect multiple subsystems")
        
        # Type-specific risks
        if req_type == RequirementType.FEATURE_ADD:
            risks.append("New feature may conflict with existing functionality")
            risks.append("May require UI changes")
        elif req_type == RequirementType.OPTIMIZATION:
            risks.append("Optimization may introduce bugs")
            risks.append("May reduce code readability")
        elif req_type == RequirementType.INTEGRATION:
            risks.append("External code may have security vulnerabilities")
            risks.append("May introduce dependency conflicts")
        
        return risks
    
    def _extract_assumptions(
        self,
        description: str,
        context: Optional[Dict[str, Any]]
    ) -> List[str]:
        """
        Extract assumptions made about the requirement.
        
        Args:
            description: Requirement description
            context: Additional context
            
        Returns:
            List of assumptions
        """
        assumptions = []
        
        # Common assumptions
        assumptions.append("User has sufficient permissions to modify code")
        assumptions.append("System dependencies are up to date")
        
        # Context-based assumptions
        if context and "current_file" in context:
            assumptions.append(f"Current file ({context['current_file']}) is the target")
        
        return assumptions
    
    def _define_success_criteria(
        self,
        req_type: RequirementType,
        description: str
    ) -> List[str]:
        """
        Define success criteria for the requirement.
        
        Args:
            req_type: Requirement type
            description: Requirement description
            
        Returns:
            List of success criteria
        """
        criteria = []
        
        # Common criteria
        criteria.append("Code compiles without errors")
        criteria.append("Existing tests pass")
        criteria.append("No regression in existing functionality")
        
        # Type-specific criteria
        if req_type == RequirementType.BUG_FIX:
            criteria.append("Bug is resolved")
            criteria.append("Edge cases are handled")
        elif req_type == RequirementType.FEATURE_ADD:
            criteria.append("New feature works as specified")
            criteria.append("Feature is properly integrated")
        elif req_type == RequirementType.OPTIMIZATION:
            criteria.append("Performance improvement is measurable")
            criteria.append("No functionality is lost")
        elif req_type == RequirementType.REFACTORING:
            criteria.append("Code quality improves")
            criteria.append("Functionality remains identical")
        
        return criteria
