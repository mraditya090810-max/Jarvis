"""
DevAgent Integrator - Automatic integration of DevAgent projects into JARVIS.

This module analyzes projects created by DevAgent and determines if they should
be integrated into JARVIS as new features or capabilities. It handles the
automatic detection, analysis, and integration of suitable projects.

Key Features:
- Automatic project analysis after DevAgent completion
- Suitability assessment for JARVIS integration
- Integration planning
- Safe integration process
- Conflict detection
"""

import sys
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from .requirement_analyzer import RequirementAnalyzer, Requirement, RequirementType
from .decision_engine import DecisionEngine, ApprovalDecision
from .status_reporter import StatusReporter


@dataclass
class ProjectAnalysis:
    """Analysis of a DevAgent project."""
    project_path: Path
    project_name: str
    description: str
    is_suitable: bool
    suitability_score: float  # 0.0 to 1.0
    integration_complexity: str
    risks: List[str] = field(default_factory=list)
    benefits: List[str] = field(default_factory=list)
    integration_plan: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "project_path": str(self.project_path),
            "project_name": self.project_name,
            "description": self.description,
            "is_suitable": self.is_suitable,
            "suitability_score": self.suitability_score,
            "integration_complexity": self.integration_complexity,
            "risks": self.risks,
            "benefits": self.benefits,
            "integration_plan": self.integration_plan,
        }


class DevAgentIntegrator:
    """
    Analyzes and integrates DevAgent projects into JARVIS.
    
    Automatically detects when DevAgent creates a project that could benefit
    JARVIS and handles the integration process.
    """
    
    # Keywords that suggest a JARVIS-relevant project
    JARVIS_KEYWORDS = [
        "jarvis", "assistant", "ai", "voice", "automation",
        "tool", "feature", "capability", "function",
        "plugin", "extension", "module", "integration",
    ]
    
    # File patterns that suggest a JARVIS tool/action
    TOOL_PATTERNS = [
        "actions/", "tools/", "modules/", "integrations/",
        "action_", "tool_", "integration_",
    ]
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the DevAgent integrator.
        
        Args:
            project_root: Path to JARVIS project root (auto-detected if None)
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        
        self.project_root = project_root
        self.requirement_analyzer = RequirementAnalyzer(project_root)
        self.decision_engine = DecisionEngine()
        self.status_reporter = StatusReporter(enable_console=True, enable_dashboard=False)
    
    def should_integrate_project(
        self,
        project_path: Path,
        project_description: Optional[str] = None
    ) -> ProjectAnalysis:
        """
        Analyze a DevAgent project to determine if it should be integrated.
        
        Args:
            project_path: Path to the DevAgent project
            project_description: Optional description of the project
            
        Returns:
            ProjectAnalysis with full assessment
        """
        print(f"[DevAgentIntegrator] Analyzing project: {project_path}")
        
        if not project_path.exists():
            return ProjectAnalysis(
                project_path=project_path,
                project_name=project_path.name,
                description="Project path does not exist",
                is_suitable=False,
                suitability_score=0.0,
                integration_complexity="N/A",
                risks=["Project path does not exist"],
            )
        
        # Extract project information
        project_name = project_path.name
        description = project_description or self._extract_project_description(project_path)
        
        # Calculate suitability score
        suitability_score = self._calculate_suitability_score(project_path, description)
        
        # Determine if suitable
        is_suitable = suitability_score >= 0.60
        
        # Analyze complexity
        integration_complexity = self._assess_integration_complexity(project_path)
        
        # Identify risks and benefits
        risks = self._identify_integration_risks(project_path)
        benefits = self._identify_integration_benefits(project_path, description)
        
        # Generate integration plan if suitable
        integration_plan = None
        if is_suitable:
            integration_plan = self._generate_integration_plan(project_path, description)
        
        return ProjectAnalysis(
            project_path=project_path,
            project_name=project_name,
            description=description,
            is_suitable=is_suitable,
            suitability_score=suitability_score,
            integration_complexity=integration_complexity,
            risks=risks,
            benefits=benefits,
            integration_plan=integration_plan,
        )
    
    def _extract_project_description(self, project_path: Path) -> str:
        """
        Extract description from project files.
        
        Args:
            project_path: Path to project
            
        Returns:
            Description string
        """
        # Look for README
        readme_files = ["README.md", "README.txt", "readme.md", "readme.txt"]
        for readme in readme_files:
            readme_path = project_path / readme
            if readme_path.exists():
                try:
                    content = readme_path.read_text(encoding="utf-8")
                    # Get first paragraph
                    lines = content.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            return line[:200]
                except Exception:
                    pass
        
        # Look for main Python file
        py_files = list(project_path.rglob("*.py"))
        if py_files:
            main_file = py_files[0]
            try:
                content = main_file.read_text(encoding="utf-8")
                # Look for docstring
                lines = content.split('\n')
                in_docstring = False
                docstring_lines = []
                for line in lines:
                    if '"""' in line or "'''" in line:
                        in_docstring = not in_docstring
                        if in_docstring:
                            continue
                    if in_docstring:
                        docstring_lines.append(line)
                        if len(docstring_lines) > 5:
                            break
                if docstring_lines:
                    return " ".join(docstring_lines)[:200]
            except Exception:
                pass
        
        return f"Project at {project_path.name}"
    
    def _calculate_suitability_score(
        self,
        project_path: Path,
        description: str
    ) -> float:
        """
        Calculate suitability score for JARVIS integration.
        
        Args:
            project_path: Path to project
            description: Project description
            
        Returns:
            Suitability score (0.0 to 1.0)
        """
        score = 0.0
        
        # Check for JARVIS-related keywords in description
        desc_lower = description.lower()
        keyword_matches = sum(1 for kw in self.JARVIS_KEYWORDS if kw in desc_lower)
        score += min(keyword_matches * 0.15, 0.40)
        
        # Check for tool/action patterns in file structure
        path_str = str(project_path).lower()
        pattern_matches = sum(1 for pat in self.TOOL_PATTERNS if pat in path_str)
        score += min(pattern_matches * 0.10, 0.30)
        
        # Check if project has Python files
        py_files = list(project_path.rglob("*.py"))
        if py_files:
            score += 0.15
        
        # Check if project has requirements/dependencies
        req_files = ["requirements.txt", "pyproject.toml", "setup.py"]
        if any((project_path / rf).exists() for rf in req_files):
            score += 0.10
        
        # Check if project has tests
        test_files = list(project_path.rglob("test_*.py")) + list(project_path.rglob("*_test.py"))
        if test_files:
            score += 0.05
        
        return min(score, 1.0)
    
    def _assess_integration_complexity(self, project_path: Path) -> str:
        """
        Assess the complexity of integrating this project.
        
        Args:
            project_path: Path to project
            
        Returns:
            Complexity level string
        """
        py_files = list(project_path.rglob("*.py"))
        file_count = len(py_files)
        
        # Check total lines of code
        total_lines = 0
        for py_file in py_files:
            try:
                total_lines += len(py_file.read_text(encoding="utf-8").split('\n'))
            except Exception:
                pass
        
        # Check dependencies
        req_file = project_path / "requirements.txt"
        dep_count = 0
        if req_file.exists():
            try:
                dep_count = len([l for l in req_file.read_text(encoding="utf-8").split('\n') if l.strip() and not l.startswith('#')])
            except Exception:
                pass
        
        # Assess complexity
        if file_count <= 2 and total_lines <= 500 and dep_count <= 3:
            return "simple"
        elif file_count <= 5 and total_lines <= 2000 and dep_count <= 10:
            return "moderate"
        elif file_count <= 10 and total_lines <= 5000 and dep_count <= 20:
            return "complex"
        else:
            return "very_complex"
    
    def _identify_integration_risks(self, project_path: Path) -> List[str]:
        """
        Identify potential risks of integrating this project.
        
        Args:
            project_path: Path to project
            
        Returns:
            List of risk descriptions
        """
        risks = []
        
        # Check for conflicting file names
        project_files = [f.name for f in project_path.rglob("*.py")]
        jarvis_files = [f.name for f in self.project_root.rglob("*.py")]
        
        conflicts = set(project_files) & set(jarvis_files)
        if conflicts:
            risks.append(f"Potential file name conflicts: {', '.join(list(conflicts)[:3])}")
        
        # Check for many dependencies
        req_file = project_path / "requirements.txt"
        if req_file.exists():
            try:
                dep_count = len([l for l in req_file.read_text(encoding="utf-8").split('\n') if l.strip() and not l.startswith('#')])
                if dep_count > 10:
                    risks.append(f"High dependency count ({dep_count} packages)")
            except Exception:
                pass
        
        # Check for network operations (security risk)
        py_files = list(project_path.rglob("*.py"))
        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8").lower()
                if "requests" in content or "urllib" in content or "http" in content:
                    risks.append("Project makes network requests")
                    break
            except Exception:
                pass
        
        return risks
    
    def _identify_integration_benefits(
        self,
        project_path: Path,
        description: str
    ) -> List[str]:
        """
        Identify potential benefits of integrating this project.
        
        Args:
            project_path: Path to project
            description: Project description
            
        Returns:
            List of benefit descriptions
        """
        benefits = []
        
        # Check if it adds a new capability
        if "new" in description.lower() or "add" in description.lower():
            benefits.append("Adds new capability to JARVIS")
        
        # Check if it improves performance
        if "fast" in description.lower() or "optimize" in description.lower() or "performance" in description.lower():
            benefits.append("May improve performance")
        
        # Check if it adds automation
        if "automate" in description.lower() or "automation" in description.lower():
            benefits.append("Adds automation capability")
        
        # Check if it has tests
        test_files = list(project_path.rglob("test_*.py")) + list(project_path.rglob("*_test.py"))
        if test_files:
            benefits.append("Includes tests for quality assurance")
        
        # Check if it's well-documented
        readme_files = ["README.md", "README.txt"]
        if any((project_path / rf).exists() for rf in readme_files):
            benefits.append("Includes documentation")
        
        return benefits
    
    def _generate_integration_plan(
        self,
        project_path: Path,
        description: str
    ) -> str:
        """
        Generate a plan for integrating the project.
        
        Args:
            project_path: Path to project
            description: Project description
            
        Returns:
            Integration plan string
        """
        plan_parts = [
            "Integration Plan:",
            "1. Review project structure and dependencies",
            "2. Identify integration points in JARVIS",
            "3. Resolve any file name conflicts",
            "4. Copy project files to appropriate location",
            "5. Update imports and references",
            "6. Add project to JARVIS tool registry",
            "7. Run integration tests",
            "8. Verify functionality",
        ]
        
        return "\n".join(plan_parts)
    
    def integrate_project(
        self,
        project_path: Path,
        auto_merge: bool = False
    ) -> Dict[str, Any]:
        """
        Integrate a DevAgent project into JARVIS.
        
        Args:
            project_path: Path to the project
            auto_merge: Whether to automatically merge (requires high confidence)
            
        Returns:
            Result dictionary
        """
        # First analyze the project
        analysis = self.should_integrate_project(project_path)
        
        if not analysis.is_suitable:
            return {
                "success": False,
                "error": "Project not suitable for integration",
                "analysis": analysis.to_dict(),
            }
        
        # Create a requirement for the integration
        requirement = Requirement(
            original_request=f"Integrate project: {analysis.project_name}",
            requirement_type=RequirementType.INTEGRATION,
            description=analysis.description,
            affected_modules=["actions"],  # Most integrations go to actions
            affected_files=[],
            dependencies=[],
            complexity=self._map_complexity(analysis.integration_complexity),
            estimated_hours=2.0,
            feasibility_score=analysis.suitability_score,
            risks=analysis.risks,
            assumptions=["Project is compatible with JARVIS architecture"],
            success_criteria=["Project integrates successfully", "No conflicts"],
        )
        
        # Make decision
        decision = self.decision_engine.evaluate(requirement)
        
        if decision.approval_decision == ApprovalDecision.REJECT:
            return {
                "success": False,
                "error": "Integration rejected due to risk",
                "analysis": analysis.to_dict(),
                "decision": decision.to_dict(),
            }
        
        if decision.approval_decision == ApprovalDecision.MANUAL_APPROVE and not auto_merge:
            return {
                "success": False,
                "error": "Manual approval required for integration",
                "analysis": analysis.to_dict(),
                "decision": decision.to_dict(),
            }
        
        # Perform integration
        # This would use the AutoMerger to actually integrate the files
        # For now, return a placeholder result
        return {
            "success": True,
            "message": "Integration approved (actual integration pending)",
            "analysis": analysis.to_dict(),
            "decision": decision.to_dict(),
        }
    
    def _map_complexity(self, complexity_str: str) -> str:
        """Map complexity string to Requirement complexity enum value."""
        from .requirement_analyzer import Complexity
        
        mapping = {
            "simple": Complexity.SIMPLE.value,
            "moderate": Complexity.MODERATE.value,
            "complex": Complexity.COMPLEX.value,
            "very_complex": Complexity.VERY_COMPLEX.value,
        }
        return mapping.get(complexity_str, Complexity.MODERATE.value)
