"""
Status Reporter - Real-time progress reporting for autonomous self-engineering.

This module provides real-time status updates during the self-improvement pipeline.
It supports multiple output channels (console, dashboard, memory) and tracks
progress through all phases of the engineering process.

Key Features:
- Phase-based progress tracking
- Multiple output channels
- Detailed status messages
- Progress percentage calculation
- Dashboard integration
- Memory persistence
- Error reporting
"""

import sys
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class EngineeringPhase(Enum):
    """Phases of the autonomous engineering pipeline."""
    IDLE = "idle"
    TRIGGER_DETECTION = "trigger_detection"
    REQUIREMENT_ANALYSIS = "requirement_analysis"
    DECISION_MAKING = "decision_making"
    FEATURE_PLANNING = "feature_planning"
    SANDBOX_SETUP = "sandbox_setup"
    CODE_GENERATION = "code_generation"
    INTEGRATION = "integration"
    DEBUGGING = "debugging"
    STATIC_ANALYSIS = "static_analysis"
    UNIT_TESTING = "unit_testing"
    INTEGRATION_TESTING = "integration_testing"
    REGRESSION_TESTING = "regression_testing"
    PERFORMANCE_BENCHMARKING = "performance_benchmarking"
    SECURITY_CHECKS = "security_checks"
    SELF_REVIEW = "self_review"
    BACKUP = "backup"
    AUTO_MERGE = "auto_merge"
    POST_APPLY_MONITORING = "post_apply_monitoring"
    ROLLBACK = "rollback"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class StatusUpdate:
    """Represents a status update."""
    phase: EngineeringPhase
    message: str
    progress: float  # 0.0 to 1.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    modified_files: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "phase": self.phase.value,
            "message": self.message,
            "progress": self.progress,
            "timestamp": self.timestamp,
            "details": self.details,
            "error": self.error,
            "modified_files": self.modified_files,
        }


class StatusReporter:
    """
    Reports status updates during the engineering pipeline.
    
    Supports multiple output channels and provides detailed progress tracking.
    """
    
    # Phase order for progress calculation
    PHASE_ORDER = [
        EngineeringPhase.IDLE,
        EngineeringPhase.TRIGGER_DETECTION,
        EngineeringPhase.REQUIREMENT_ANALYSIS,
        EngineeringPhase.DECISION_MAKING,
        EngineeringPhase.FEATURE_PLANNING,
        EngineeringPhase.SANDBOX_SETUP,
        EngineeringPhase.CODE_GENERATION,
        EngineeringPhase.INTEGRATION,
        EngineeringPhase.DEBUGGING,
        EngineeringPhase.STATIC_ANALYSIS,
        EngineeringPhase.UNIT_TESTING,
        EngineeringPhase.INTEGRATION_TESTING,
        EngineeringPhase.REGRESSION_TESTING,
        EngineeringPhase.PERFORMANCE_BENCHMARKING,
        EngineeringPhase.SECURITY_CHECKS,
        EngineeringPhase.SELF_REVIEW,
        EngineeringPhase.BACKUP,
        EngineeringPhase.AUTO_MERGE,
        EngineeringPhase.POST_APPLY_MONITORING,
        EngineeringPhase.COMPLETE,
    ]
    
    def __init__(
        self,
        enable_console: bool = True,
        enable_dashboard: bool = True,
        enable_memory: bool = True,
        dashboard_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """
        Initialize the status reporter.
        
        Args:
            enable_console: Enable console output
            enable_dashboard: Enable dashboard updates
            enable_memory: Enable memory persistence
            dashboard_callback: Callback for dashboard updates
        """
        self.enable_console = enable_console
        self.enable_dashboard = enable_dashboard
        self.enable_memory = enable_memory
        self.dashboard_callback = dashboard_callback
        
        self.current_phase = EngineeringPhase.IDLE
        self.start_time: Optional[datetime] = None
        self.phase_history: List[StatusUpdate] = []
        self.modified_files: List[str] = []
        
        # Progress tracking
        self.phase_progress: Dict[EngineeringPhase, float] = {
            phase: 0.0 for phase in self.PHASE_ORDER
        }
    
    def start(self) -> None:
        """Start the engineering process."""
        self.start_time = datetime.now()
        self.current_phase = EngineeringPhase.TRIGGER_DETECTION
        self.phase_history.clear()
        self.modified_files.clear()
        
        self.report(
            EngineeringPhase.TRIGGER_DETECTION,
            "Starting autonomous self-engineering pipeline",
            progress=0.0
        )
    
    def report(
        self,
        phase: EngineeringPhase,
        message: str,
        progress: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        modified_files: Optional[List[str]] = None
    ) -> None:
        """
        Report a status update.
        
        Args:
            phase: Current engineering phase
            message: Status message
            progress: Progress within this phase (0.0 to 1.0)
            details: Additional details
            error: Error message if applicable
            modified_files: List of modified files
        """
        # Update current phase
        if phase != self.current_phase:
            self.current_phase = phase
        
        # Calculate overall progress
        if progress is not None:
            self.phase_progress[phase] = progress
        overall_progress = self._calculate_overall_progress()
        
        # Update modified files
        if modified_files:
            self.modified_files.extend(modified_files)
            self.modified_files = list(set(self.modified_files))  # Deduplicate
        
        # Create status update
        update = StatusUpdate(
            phase=phase,
            message=message,
            progress=overall_progress,
            details=details or {},
            error=error,
            modified_files=self.modified_files.copy(),
        )
        
        # Add to history
        self.phase_history.append(update)
        
        # Output to all enabled channels
        self._output(update)
    
    def report_phase_complete(
        self,
        phase: EngineeringPhase,
        message: str = "Phase complete"
    ) -> None:
        """Report that a phase is complete."""
        self.phase_progress[phase] = 1.0
        self.report(phase, message, progress=1.0)
    
    def report_error(
        self,
        phase: EngineeringPhase,
        error: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Report an error."""
        self.report(
            phase,
            f"Error: {error}",
            details=details,
            error=error
        )
    
    def complete(self, message: str = "Engineering pipeline complete") -> None:
        """Mark the pipeline as complete."""
        self.report(
            EngineeringPhase.COMPLETE,
            message,
            progress=1.0
        )
        self.current_phase = EngineeringPhase.COMPLETE
    
    def fail(self, error: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Mark the pipeline as failed."""
        self.report(
            EngineeringPhase.FAILED,
            f"Pipeline failed: {error}",
            error=error,
            details=details
        )
        self.current_phase = EngineeringPhase.FAILED
    
    def _calculate_overall_progress(self) -> float:
        """Calculate overall progress across all phases."""
        total_phases = len(self.PHASE_ORDER)
        if total_phases == 0:
            return 0.0
        
        # Find current phase index
        try:
            current_idx = self.PHASE_ORDER.index(self.current_phase)
        except ValueError:
            return 0.0
        
        # Calculate progress as weighted sum of completed phases + current phase progress
        completed_weight = current_idx / total_phases
        current_progress = self.phase_progress.get(self.current_phase, 0.0)
        current_weight = current_progress / total_phases
        
        return completed_weight + current_weight
    
    def _output(self, update: StatusUpdate) -> None:
        """Output status update to all enabled channels."""
        # Console output
        if self.enable_console:
            self._console_output(update)
        
        # Dashboard output
        if self.enable_dashboard and self.dashboard_callback:
            self._dashboard_output(update)
        
        # Memory output
        if self.enable_memory:
            self._memory_output(update)
    
    def _console_output(self, update: StatusUpdate) -> None:
        """Output to console."""
        progress_pct = int(update.progress * 100)
        phase_name = update.phase.value.replace('_', ' ').title()
        
        if update.error:
            # Error output
            print(f"[Self-Engineering] ❌ [{phase_name}] {update.message}")
            if update.error:
                print(f"[Self-Engineering]    Error: {update.error}")
        else:
            # Normal output
            print(f"[Self-Engineering] [{progress_pct:3d}%] [{phase_name}] {update.message}")
        
        # Show details if present
        if update.details:
            for key, value in update.details.items():
                print(f"[Self-Engineering]    {key}: {value}")
    
    def _dashboard_output(self, update: StatusUpdate) -> None:
        """Output to dashboard via callback."""
        try:
            if self.dashboard_callback:
                self.dashboard_callback(update.to_dict())
        except Exception as e:
            print(f"[StatusReporter] Dashboard output failed: {e}")
    
    def _memory_output(self, update: StatusUpdate) -> None:
        """Output to memory for persistence."""
        try:
            from memory import update_memory
            
            # Store recent status in memory
            memory_update = {
                "engineering_status": {
                    "current_phase": update.phase.value,
                    "message": update.message,
                    "progress": update.progress,
                    "timestamp": update.timestamp,
                    "error": update.error,
                }
            }
            update_memory(memory_update)
        except Exception as e:
            print(f"[StatusReporter] Memory output failed: {e}")
    
    def get_current_status(self) -> Dict[str, Any]:
        """Get current status summary."""
        elapsed_time = None
        if self.start_time:
            elapsed_time = (datetime.now() - self.start_time).total_seconds()
        
        return {
            "current_phase": self.current_phase.value,
            "progress": self._calculate_overall_progress(),
            "elapsed_time": elapsed_time,
            "modified_files": self.modified_files,
            "start_time": self.start_time.isoformat() if self.start_time else None,
        }
    
    def get_history(self) -> List[Dict[str, Any]]:
        """Get full status history."""
        return [update.to_dict() for update in self.phase_history]
    
    def save_report(self, output_path: Optional[Path] = None) -> Path:
        """
        Save a detailed report of the engineering run.
        
        Args:
            output_path: Path to save report (auto-generated if None)
            
        Returns:
            Path where report was saved
        """
        if output_path is None:
            output_path = Path(__file__).parent.parent / "reports" / f"engineering_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        report = {
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": datetime.now().isoformat(),
            "current_phase": self.current_phase.value,
            "modified_files": self.modified_files,
            "history": self.get_history(),
            "final_status": self.get_current_status(),
        }
        
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[StatusReporter] Report saved to: {output_path}")
        
        return output_path


class ProgressTracker:
    """
    Helper class for tracking progress within a phase.
    
    Usage:
        tracker = ProgressTracker(reporter, EngineeringPhase.CODE_GENERATION, total_steps=10)
        for i in range(10):
            # Do work
            tracker.step(f"Processing file {i}")
    """
    
    def __init__(
        self,
        reporter: StatusReporter,
        phase: EngineeringPhase,
        total_steps: int,
        start_message: Optional[str] = None
    ):
        """
        Initialize progress tracker.
        
        Args:
            reporter: StatusReporter instance
            phase: Current engineering phase
            total_steps: Total number of steps in this phase
            start_message: Optional message to report on start
        """
        self.reporter = reporter
        self.phase = phase
        self.total_steps = total_steps
        self.current_step = 0
        
        if start_message:
            self.reporter.report(phase, start_message, progress=0.0)
    
    def step(self, message: str) -> None:
        """Report completion of a step."""
        self.current_step += 1
        progress = self.current_step / self.total_steps if self.total_steps > 0 else 1.0
        self.reporter.report(self.phase, message, progress=progress)
    
    def complete(self, message: str = "Complete") -> None:
        """Mark phase as complete."""
        self.reporter.report_phase_complete(self.phase, message)
