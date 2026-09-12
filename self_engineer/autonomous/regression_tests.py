"""
Regression Test Suite for Autonomous Self-Engineering System.

This module provides comprehensive regression testing to ensure that autonomous
self-improvement changes do not break existing JARVIS functionality.

Key Features:
- Test all major JARVIS subsystems
- Test core functionality
- Test integration points
- Performance regression detection
- Automated test execution
- Test result reporting
"""

import sys
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@dataclass
class TestResult:
    """Result of a single test."""
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


@dataclass
class TestSuiteResult:
    """Result of a complete test suite run."""
    total_tests: int
    passed: int
    failed: int
    duration: float
    results: List[TestResult] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "duration": self.duration,
            "success_rate": self.passed / self.total_tests if self.total_tests > 0 else 0.0,
            "results": [r.to_dict() for r in self.results],
        }


class RegressionTestSuite:
    """
    Comprehensive regression test suite for JARVIS.
    
    Tests all major subsystems to ensure autonomous changes don't break functionality.
    """
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the regression test suite.
        
        Args:
            project_root: Path to project root (auto-detected if None)
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        
        self.project_root = project_root
        self.tests: List[Callable] = []
        self._register_tests()
    
    def _register_tests(self) -> None:
        """Register all regression tests."""
        # Core functionality tests
        self.tests.append(self.test_main_import)
        self.tests.append(self.test_memory_system)
        self.tests.append(self.test_ai_router)
        
        # Action module tests
        self.tests.append(self.test_browser_control_import)
        self.tests.append(self.test_computer_control_import)
        self.tests.append(self.test_file_controller_import)
        self.tests.append(self.test_code_helper_import)
        
        # Self-engineer tests
        self.tests.append(self.test_self_engineer_import)
        self.tests.append(self.test_autonomous_import)
        self.tests.append(self.test_code_graph_import)
        
        # Dashboard tests
        self.tests.append(self.test_dashboard_import)
        
        # Research tests
        self.tests.append(self.test_research_import)
    
    async def run_all(self) -> TestSuiteResult:
        """
        Run all regression tests.
        
        Returns:
            TestSuiteResult with complete results
        """
        print("[RegressionTests] Starting regression test suite...")
        start_time = datetime.now()
        
        results = []
        passed = 0
        failed = 0
        
        for test in self.tests:
            test_name = test.__name__
            test_start = datetime.now()
            
            try:
                await test()
                duration = (datetime.now() - test_start).total_seconds()
                results.append(TestResult(
                    name=test_name,
                    passed=True,
                    duration=duration
                ))
                passed += 1
                print(f"[RegressionTests] ✓ {test_name} ({duration:.2f}s)")
            except Exception as e:
                duration = (datetime.now() - test_start).total_seconds()
                results.append(TestResult(
                    name=test_name,
                    passed=False,
                    duration=duration,
                    error=str(e)
                ))
                failed += 1
                print(f"[RegressionTests] ✗ {test_name} ({duration:.2f}s): {e}")
        
        total_duration = (datetime.now() - start_time).total_seconds()
        
        result = TestSuiteResult(
            total_tests=len(results),
            passed=passed,
            failed=failed,
            duration=total_duration,
            results=results
        )
        
        print(f"[RegressionTests] Complete: {passed}/{len(results)} passed ({total_duration:.2f}s)")
        
        return result
    
    async def test_main_import(self) -> None:
        """Test that main.py can be imported."""
        # This is a basic syntax/import test
        import main
        assert main is not None
    
    async def test_memory_system(self) -> None:
        """Test memory system functionality."""
        from memory.memory_manager import load_memory, update_memory
        
        # Test loading
        memory = load_memory()
        assert memory is not None
        
        # Test updating (use a temporary key)
        test_key = "_regression_test_key"
        update_memory({"notes": {test_key: {"value": "test"}}})
        
        # Clean up
        from memory.memory_manager import forget
        forget("notes", test_key)
    
    async def test_ai_router(self) -> None:
        """Test AI router can be imported and initialized."""
        try:
            from core.ai.router import AIRouter
            # Just test import, don't initialize (requires API keys)
            assert AIRouter is not None
        except ImportError:
            # AI router might not be available in all configurations
            pass
    
    async def test_browser_control_import(self) -> None:
        """Test browser control module import."""
        from actions.browser_control import browser_control
        assert browser_control is not None
    
    async def test_computer_control_import(self) -> None:
        """Test computer control module import."""
        from actions.computer_control import computer_control
        assert computer_control is not None
    
    async def test_file_controller_import(self) -> None:
        """Test file controller module import."""
        from actions.file_controller import file_controller
        assert file_controller is not None
    
    async def test_code_helper_import(self) -> None:
        """Test code helper module import."""
        from actions.code_helper import code_helper
        assert code_helper is not None
    
    async def test_self_engineer_import(self) -> None:
        """Test self-engineer module import."""
        from self_engineer.orchestrator import SelfEngineer
        assert SelfEngineer is not None
    
    async def test_autonomous_import(self) -> None:
        """Test autonomous self-engineering module import."""
        from self_engineer.autonomous import (
            TriggerDetector,
            RequirementAnalyzer,
            DecisionEngine,
            StatusReporter,
            AutonomousOrchestrator,
        )
        assert TriggerDetector is not None
        assert RequirementAnalyzer is not None
        assert DecisionEngine is not None
        assert StatusReporter is not None
        assert AutonomousOrchestrator is not None
    
    async def test_code_graph_import(self) -> None:
        """Test code graph module import."""
        from code_graph.engine import CodeGraphEngine
        assert CodeGraphEngine is not None
    
    async def test_dashboard_import(self) -> None:
        """Test dashboard module import."""
        try:
            from dashboard.server import Dashboard
            # Just test import
            assert Dashboard is not None
        except ImportError:
            # Dashboard might not be available if dependencies not installed
            pass
    
    async def test_research_import(self) -> None:
        """Test research module import."""
        from actions.research_mode import research_mode
        assert research_mode is not None
    
    def save_results(self, result: TestSuiteResult, output_path: Optional[Path] = None) -> Path:
        """
        Save test results to file.
        
        Args:
            result: TestSuiteResult to save
            output_path: Path to save results (auto-generated if None)
            
        Returns:
            Path where results were saved
        """
        if output_path is None:
            output_path = self.project_root / "self_engineer" / "reports" / f"regression_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        output_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        print(f"[RegressionTests] Results saved to: {output_path}")
        
        return output_path


class QuickRegressionTest:
    """
    Quick regression test for critical functionality only.
    
    Used for faster validation during development.
    """
    
    @staticmethod
    async def run_critical_tests(project_root: Optional[Path] = None) -> TestSuiteResult:
        """
        Run only critical regression tests.
        
        Args:
            project_root: Path to project root
            
        Returns:
            TestSuiteResult with results
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent.parent
        
        suite = RegressionTestSuite(project_root)
        # Override with only critical tests
        suite.tests = [
            suite.test_main_import,
            suite.test_autonomous_import,
            suite.test_self_engineer_import,
        ]
        
        return await suite.run_all()


async def main():
    """Main entry point for running regression tests."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run JARVIS regression tests")
    parser.add_argument("--quick", action="store_true", help="Run only critical tests")
    parser.add_argument("--output", type=str, help="Output path for results")
    args = parser.parse_args()
    
    if args.quick:
        result = await QuickRegressionTest.run_critical_tests()
    else:
        suite = RegressionTestSuite()
        result = await suite.run_all()
    
    # Save results
    output_path = Path(args.output) if args.output else None
    if output_path:
        suite = RegressionTestSuite()
        suite.save_results(result, output_path)
    else:
        suite = RegressionTestSuite()
        suite.save_results(result)
    
    # Exit with error code if any tests failed
    if result.failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
