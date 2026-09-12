"""
Autonomous Self Engineering System

This package provides the autonomous layer for JARVIS self-improvement.
It extends the supervised self_engineer module with automatic trigger detection,
requirement understanding, and autonomous decision making.

Modules:
- trigger_detector: Detect self-improvement requests in natural language
- requirement_analyzer: Understand user intent and requirements
- decision_engine: Risk/benefit analysis and confidence scoring
- status_reporter: Real-time progress reporting
- orchestrator: Main autonomous pipeline coordinator
- auto_merger: Safe automatic application of changes
- devagent_integrator: Automatic integration of DevAgent projects
"""

from .trigger_detector import TriggerDetector, TriggerType
from .requirement_analyzer import RequirementAnalyzer
from .decision_engine import DecisionEngine, Decision, ConfidenceLevel
from .status_reporter import StatusReporter, EngineeringPhase
from .orchestrator import AutonomousOrchestrator
from .auto_merger import AutoMerger
from .devagent_integrator import DevAgentIntegrator

__all__ = [
    "TriggerDetector",
    "TriggerType",
    "RequirementAnalyzer",
    "DecisionEngine",
    "Decision",
    "ConfidenceLevel",
    "StatusReporter",
    "EngineeringPhase",
    "AutonomousOrchestrator",
    "AutoMerger",
    "DevAgentIntegrator",
]
