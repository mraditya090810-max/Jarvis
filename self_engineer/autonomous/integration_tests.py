"""
Integration Test Suite for Autonomous Self-Engineering System.

This module provides integration tests to verify that the autonomous
self-engineering components work together correctly.

Key Features:
- Test component integration
- Test end-to-end pipeline
- Test trigger detection
- Test decision making
- Test orchestrator flow
"""

import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from .trigger_detector import TriggerDetector, TriggerType
from .requirement_analyzer import RequirementAnalyzer
from .decision_engine import DecisionEngine, ApprovalDecision
from .status_reporter import StatusReporter, EngineeringPhase


@dataclass
class IntegrationTestResult:
    """Result of an integration test."""
    name: str
    passed: bool
    duration: float
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "passed": self.passed,
            "duration": self.duration,
            "error": self.error,
            "details": self.details,
        }


class IntegrationTestSuite:
    """
    Integration test suite for autonomous self-engineering.
    
    Tests that components work together correctly.
    """
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the integration test suite.
        
        Args:
            project_root: Path to project root (auto-detected if None)
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        
        self.project_root = project_root
    
    async def run_all(self) -> List[IntegrationTestResult]:
        """
        Run all integration tests.
        
        Returns:
            List of IntegrationTestResult
        """
        print("[IntegrationTests] Starting integration test suite...")
        
        results = []
        
        # Test trigger detection
        results.append(await self.test_trigger_detection())
        
        # Test requirement analysis
        results.append(await self.test_requirement_analysis())
        
        # Test decision engine
        results.append(await self.test_decision_engine())
        
        # Test status reporter
        results.append(await self.test_status_reporter())
        
        # Test end-to-end flow
        results.append(await self.test_end_to_end_flow())
        
        passed = sum(1 for r in results if r.passed)
        print(f"[IntegrationTests] Complete: {passed}/{len(results)} passed")
        
        return results
    
    async def test_trigger_detection(self) -> IntegrationTestResult:
        """Test trigger detection functionality."""
        start_time = datetime.now()
        
        try:
            detector = TriggerDetector(use_ai=False)  # Disable AI for faster testing
            
            # Test pattern-based detection
            trigger, confidence = detector.detect_from_text("improve yourself")
            assert trigger == TriggerType.IMPROVE_SELF
            assert confidence >= 0.85
            
            trigger, confidence = detector.detect_from_text("add feature to play music")
            assert trigger == TriggerType.ADD_FEATURE
            assert confidence >= 0.85
            
            trigger, confidence = detector.detect_from_text("fix this bug")
            assert trigger == TriggerType.FIX_BUG
            assert confidence >= 0.85
            
            # Test no trigger
            trigger, confidence = detector.detect_from_text("what is the weather")
            assert trigger is None
            assert confidence == 0.0
            
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_trigger_detection",
                passed=True,
                duration=duration
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_trigger_detection",
                passed=False,
                duration=duration,
                error=str(e)
            )
    
    async def test_requirement_analysis(self) -> IntegrationTestResult:
        """Test requirement analysis functionality."""
        start_time = datetime.now()
        
        try:
            analyzer = RequirementAnalyzer(self.project_root)
            
            # Test simple requirement
            requirement = analyzer.analyze(
                "fix the bug in file_controller",
                "fix_bug",
                context={}
            )
            
            assert requirement is not None
            assert requirement.requirement_type.value == "bug_fix"
            assert len(requirement.affected_modules) > 0
            assert requirement.complexity is not None
            
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_requirement_analysis",
                passed=True,
                duration=duration,
                details={"requirement_type": requirement.requirement_type.value}
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_requirement_analysis",
                passed=False,
                duration=duration,
                error=str(e)
            )
    
    async def test_decision_engine(self) -> IntegrationTestResult:
        """Test decision engine functionality."""
        start_time = datetime.now()
        
        try:
            from .requirement_analyzer import Requirement, RequirementType, Complexity
            
            engine = DecisionEngine(auto_approve_threshold=0.75)
            
            # Create a low-risk requirement
            requirement = Requirement(
                original_request="fix unused import",
                requirement_type=RequirementType.BUG_FIX,
                description="Fix unused import in a single file",
                affected_modules=["actions"],
                affected_files=["actions/test.py"],
                dependencies=[],
                complexity=Complexity.TRIVIAL,
                estimated_hours=0.5,
                feasibility_score=0.95,
                risks=[],
                assumptions=[],
                success_criteria=[]
            )
            
            decision = engine.evaluate(requirement)
            
            assert decision is not None
            assert decision.confidence_score >= 0.75  # Should be high confidence
            assert decision.approval_decision in [ApprovalDecision.AUTO_APPROVE, ApprovalDecision.AUTO_APPROVE_MONITOR]
            
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_decision_engine",
                passed=True,
                duration=duration,
                details={
                    "confidence_score": decision.confidence_score,
                    "approval_decision": decision.approval_decision.value
                }
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_decision_engine",
                passed=False,
                duration=duration,
                error=str(e)
            )
    
    async def test_status_reporter(self) -> IntegrationTestResult:
        """Test status reporter functionality."""
        start_time = datetime.now()
        
        try:
            reporter = StatusReporter(enable_console=False, enable_dashboard=False, enable_memory=False)
            
            reporter.start()
            
            # Test reporting
            reporter.report(
                EngineeringPhase.TRIGGER_DETECTION,
                "Test message",
                progress=0.5
            )
            
            reporter.report_phase_complete(EngineeringPhase.TRIGGER_DETECTION)
            
            # Test status retrieval
            status = reporter.get_current_status()
            assert status is not None
            assert "current_phase" in status
            assert "progress" in status
            
            reporter.complete()
            
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_status_reporter",
                passed=True,
                duration=duration
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_status_reporter",
                passed=False,
                duration=duration,
                error=str(e)
            )
    
    async def test_end_to_end_flow(self) -> IntegrationTestResult:
        """Test end-to-end integration flow."""
        start_time = datetime.now()
        
        try:
            # Initialize components
            detector = TriggerDetector(use_ai=False)
            analyzer = RequirementAnalyzer(self.project_root)
            engine = DecisionEngine(auto_approve_threshold=0.75)
            reporter = StatusReporter(enable_console=False, enable_dashboard=False, enable_memory=False)
            
            # Simulate a request
            request = "fix the unused import in file_controller"
            
            # Step 1: Trigger detection
            trigger, confidence = detector.detect_from_text(request)
            assert trigger is not None
            
            # Step 2: Requirement analysis
            requirement = analyzer.analyze(request, trigger.value, context={})
            assert requirement is not None
            
            # Step 3: Decision making
            decision = engine.evaluate(requirement)
            assert decision is not None
            
            # Step 4: Status reporting
            reporter.start()
            reporter.report(EngineeringPhase.TRIGGER_DETECTION, "Trigger detected", progress=0.1)
            reporter.report(EngineeringPhase.REQUIREMENT_ANALYSIS, "Requirement analyzed", progress=0.3)
            reporter.report(EngineeringPhase.DECISION_MAKING, "Decision made", progress=0.5)
            reporter.complete()
            
            status = reporter.get_current_status()
            assert status["current_phase"] == "complete"
            
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_end_to_end_flow",
                passed=True,
                duration=duration,
                details={
                    "trigger_type": trigger.value,
                    "requirement_type": requirement.requirement_type.value,
                    "decision": decision.approval_decision.value
                }
            )
            
        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds()
            return IntegrationTestResult(
                name="test_end_to_end_flow",
                passed=False,
                duration=duration,
                error=str(e)
            )
    
    def save_results(self, results: List[IntegrationTestResult], output_path: Optional[Path] = None) -> Path:
        """
        Save test results to file.
        
        Args:
            results: List of IntegrationTestResult
            output_path: Path to save results (auto-generated if None)
            
        Returns:
            Path where results were saved
        """
        if output_path is None:
            output_path = self.project_root / "self_engineer" / "reports" / f"integration_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        results_dict = {
            "total_tests": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [r.to_dict() for r in results],
        }
        
        output_path.write_text(json.dumps(results_dict, indent=2), encoding="utf-8")
        print(f"[IntegrationTests] Results saved to: {output_path}")
        
        return output_path


async def main():
    """Main entry point for running integration tests."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run JARVIS integration tests")
    parser.add_argument("--output", type=str, help="Output path for results")
    args = parser.parse_args()
    
    suite = IntegrationTestSuite()
    results = await suite.run_all()
    
    # Save results
    output_path = Path(args.output) if args.output else None
    suite.save_results(results, output_path)
    
    # Exit with error code if any tests failed
    if any(not r.passed for r in results):
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
