"""
Decision Engine - Risk/benefit analysis and confidence scoring for autonomous decisions.

This module evaluates whether a self-improvement change should be applied autonomously
or requires manual approval. It analyzes risk factors, benefits, complexity, and
historical success rates to make informed decisions.

Key Features:
- Multi-factor risk analysis
- Benefit assessment
- Confidence scoring (0.0 to 1.0)
- Historical learning from past improvements
- Configurable risk thresholds
- Approval recommendation
"""

import sys
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from .requirement_analyzer import Requirement, RequirementType, Complexity


class ConfidenceLevel(Enum):
    """Confidence levels for autonomous decisions."""
    VERY_HIGH = "very_high"  # > 0.90 - Safe to auto-apply
    HIGH = "high"            # 0.75 - 0.90 - Safe to auto-apply
    MEDIUM = "medium"        # 0.50 - 0.75 - Consider auto-apply
    LOW = "low"              # 0.25 - 0.50 - Manual approval recommended
    VERY_LOW = "very_low"    # < 0.25 - Manual approval required


class ApprovalDecision(Enum):
    """Approval decision types."""
    AUTO_APPROVE = "auto_approve"        # Safe to apply automatically
    AUTO_APPROVE_MONITOR = "auto_approve_monitor"  # Apply with monitoring
    MANUAL_APPROVE = "manual_approve"    # Requires manual approval
    REJECT = "reject"                    # Too risky, reject


@dataclass
class Decision:
    """Represents a decision about a self-improvement request."""
    requirement: Requirement
    confidence_score: float
    confidence_level: ConfidenceLevel
    approval_decision: ApprovalDecision
    risk_factors: Dict[str, float] = field(default_factory=dict)
    benefit_factors: Dict[str, float] = field(default_factory=dict)
    reasoning: str = ""
    recommendation: str = ""
    conditions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "confidence_score": self.confidence_score,
            "confidence_level": self.confidence_level.value,
            "approval_decision": self.approval_decision.value,
            "risk_factors": self.risk_factors,
            "benefit_factors": self.benefit_factors,
            "reasoning": self.reasoning,
            "recommendation": self.recommendation,
            "conditions": self.conditions,
        }


class DecisionEngine:
    """
    Evaluates self-improvement requests and makes approval decisions.
    
    Uses multi-factor analysis to determine if a change can be applied autonomously.
    """
    
    # Risk thresholds (configurable)
    AUTO_APPROVE_THRESHOLD = 0.75
    AUTO_APPROVE_MONITOR_THRESHOLD = 0.50
    MANUAL_APPROVE_THRESHOLD = 0.25
    
    # Risk factor weights
    RISK_WEIGHTS = {
        "complexity": 0.30,
        "core_module": 0.25,
        "dependencies": 0.20,
        "type_risk": 0.15,
        "historical": 0.10,
    }
    
    # Benefit factor weights
    BENEFIT_WEIGHTS = {
        "user_value": 0.35,
        "performance": 0.25,
        "maintainability": 0.20,
        "bug_fix": 0.20,
    }
    
    def __init__(
        self,
        auto_approve_threshold: float = 0.75,
        enable_learning: bool = True
    ):
        """
        Initialize the decision engine.
        
        Args:
            auto_approve_threshold: Minimum confidence for auto-approval
            enable_learning: Whether to learn from historical outcomes
        """
        self.auto_approve_threshold = auto_approve_threshold
        self.enable_learning = enable_learning
        
        # Historical data (would be persisted in production)
        self.historical_success_rate = 0.85  # Starting assumption
        self.historical_data: List[Dict[str, Any]] = []
    
    def evaluate(self, requirement: Requirement) -> Decision:
        """
        Evaluate a requirement and make a decision.
        
        Args:
            requirement: Analyzed requirement
            
        Returns:
            Decision object with full analysis
        """
        print(f"[DecisionEngine] Evaluating requirement...")
        
        # Calculate risk factors
        risk_factors = self._calculate_risk_factors(requirement)
        total_risk = sum(
            risk_factors[factor] * self.RISK_WEIGHTS.get(factor, 0.0)
            for factor in risk_factors
        )
        
        # Calculate benefit factors
        benefit_factors = self._calculate_benefit_factors(requirement)
        total_benefit = sum(
            benefit_factors[factor] * self.BENEFIT_WEIGHTS.get(factor, 0.0)
            for factor in benefit_factors
        )
        
        # Calculate confidence score
        confidence_score = self._calculate_confidence(
            total_risk, total_benefit, requirement
        )
        
        # Determine confidence level
        confidence_level = self._determine_confidence_level(confidence_score)
        
        # Make approval decision
        approval_decision = self._make_approval_decision(
            confidence_level, requirement
        )
        
        # Generate reasoning and recommendation
        reasoning, recommendation, conditions = self._generate_explanation(
            confidence_level, approval_decision, risk_factors, benefit_factors
        )
        
        return Decision(
            requirement=requirement,
            confidence_score=confidence_score,
            confidence_level=confidence_level,
            approval_decision=approval_decision,
            risk_factors=risk_factors,
            benefit_factors=benefit_factors,
            reasoning=reasoning,
            recommendation=recommendation,
            conditions=conditions,
        )
    
    def _calculate_risk_factors(self, requirement: Requirement) -> Dict[str, float]:
        """Calculate individual risk factors."""
        risk_factors = {}
        
        # Complexity risk (0.0 = low risk, 1.0 = high risk)
        complexity_map = {
            Complexity.TRIVIAL: 0.05,
            Complexity.SIMPLE: 0.15,
            Complexity.MODERATE: 0.40,
            Complexity.COMPLEX: 0.70,
            Complexity.VERY_COMPLEX: 0.95,
        }
        risk_factors["complexity"] = complexity_map.get(
            requirement.complexity, 0.40
        )
        
        # Core module risk
        core_modules = ["main", "core", "self_engineer"]
        if any(mod in requirement.affected_modules for mod in core_modules):
            risk_factors["core_module"] = 0.80
        else:
            risk_factors["core_module"] = 0.20
        
        # Dependency risk (more dependencies = higher risk)
        dep_count = len(requirement.dependencies)
        risk_factors["dependencies"] = min(dep_count / 10.0, 1.0)
        
        # Type-based risk
        type_risk_map = {
            RequirementType.BUG_FIX: 0.20,
            RequirementType.CONFIG_CHANGE: 0.15,
            RequirementType.DEPENDENCY_UPDATE: 0.30,
            RequirementType.OPTIMIZATION: 0.50,
            RequirementType.FEATURE_ADD: 0.60,
            RequirementType.REFACTORING: 0.70,
            RequirementType.INTEGRATION: 0.80,
        }
        risk_factors["type_risk"] = type_risk_map.get(
            requirement.requirement_type, 0.50
        )
        
        # Historical risk (based on past success rate)
        if self.enable_learning:
            risk_factors["historical"] = 1.0 - self.historical_success_rate
        else:
            risk_factors["historical"] = 0.15  # Conservative default
        
        return risk_factors
    
    def _calculate_benefit_factors(self, requirement: Requirement) -> Dict[str, float]:
        """Calculate individual benefit factors."""
        benefit_factors = {}
        
        # User value (estimated from description length and type)
        desc_length = len(requirement.description)
        benefit_factors["user_value"] = min(desc_length / 500.0, 1.0)
        
        # Performance benefit
        if requirement.requirement_type == RequirementType.OPTIMIZATION:
            benefit_factors["performance"] = 0.90
        else:
            benefit_factors["performance"] = 0.30
        
        # Maintainability benefit
        if requirement.requirement_type == RequirementType.REFACTORING:
            benefit_factors["maintainability"] = 0.85
        elif requirement.requirement_type == RequirementType.BUG_FIX:
            benefit_factors["maintainability"] = 0.50
        else:
            benefit_factors["maintainability"] = 0.40
        
        # Bug fix benefit
        if requirement.requirement_type == RequirementType.BUG_FIX:
            benefit_factors["bug_fix"] = 0.95
        else:
            benefit_factors["bug_fix"] = 0.20
        
        return benefit_factors
    
    def _calculate_confidence(
        self,
        total_risk: float,
        total_benefit: float,
        requirement: Requirement
    ) -> float:
        """
        Calculate overall confidence score.
        
        Args:
            total_risk: Total risk score (0.0 to 1.0)
            total_benefit: Total benefit score (0.0 to 1.0)
            requirement: Requirement being evaluated
            
        Returns:
            Confidence score (0.0 to 1.0)
        """
        # Base confidence from benefit vs risk
        if total_risk > 0:
            benefit_risk_ratio = total_benefit / total_risk
        else:
            benefit_risk_ratio = 2.0  # High confidence if no risk
        
        # Normalize to 0-1 range
        base_confidence = min(benefit_risk_ratio / 2.0, 1.0)
        
        # Adjust by feasibility
        base_confidence *= requirement.feasibility_score
        
        # Adjust by historical success rate
        if self.enable_learning:
            base_confidence *= (0.5 + 0.5 * self.historical_success_rate)
        
        return max(0.0, min(1.0, base_confidence))
    
    def _determine_confidence_level(self, score: float) -> ConfidenceLevel:
        """Determine confidence level from score."""
        if score >= 0.90:
            return ConfidenceLevel.VERY_HIGH
        elif score >= 0.75:
            return ConfidenceLevel.HIGH
        elif score >= 0.50:
            return ConfidenceLevel.MEDIUM
        elif score >= 0.25:
            return ConfidenceLevel.LOW
        else:
            return ConfidenceLevel.VERY_LOW
    
    def _make_approval_decision(
        self,
        confidence_level: ConfidenceLevel,
        requirement: Requirement
    ) -> ApprovalDecision:
        """
        Make approval decision based on confidence and other factors.
        
        Args:
            confidence_level: Calculated confidence level
            requirement: Requirement being evaluated
            
        Returns:
            Approval decision
        """
        # Very high confidence - auto approve
        if confidence_level == ConfidenceLevel.VERY_HIGH:
            return ApprovalDecision.AUTO_APPROVE
        
        # High confidence - auto approve with monitoring
        if confidence_level == ConfidenceLevel.HIGH:
            return ApprovalDecision.AUTO_APPROVE_MONITOR
        
        # Medium confidence - depends on complexity
        if confidence_level == ConfidenceLevel.MEDIUM:
            if requirement.complexity in [Complexity.TRIVIAL, Complexity.SIMPLE]:
                return ApprovalDecision.AUTO_APPROVE_MONITOR
            else:
                return ApprovalDecision.MANUAL_APPROVE
        
        # Low or very low confidence - manual approval
        if confidence_level in [ConfidenceLevel.LOW, ConfidenceLevel.VERY_LOW]:
            # Check if it's a critical bug fix (might override)
            if requirement.requirement_type == RequirementType.BUG_FIX:
                return ApprovalDecision.MANUAL_APPROVE
            else:
                return ApprovalDecision.REJECT
        
        return ApprovalDecision.MANUAL_APPROVE
    
    def _generate_explanation(
        self,
        confidence_level: ConfidenceLevel,
        approval_decision: ApprovalDecision,
        risk_factors: Dict[str, float],
        benefit_factors: Dict[str, float]
    ) -> tuple[str, str, List[str]]:
        """
        Generate explanation, recommendation, and conditions.
        
        Args:
            confidence_level: Confidence level
            approval_decision: Approval decision
            risk_factors: Risk factor scores
            benefit_factors: Benefit factor scores
            
        Returns:
            Tuple of (reasoning, recommendation, conditions)
        """
        # Build reasoning
        reasoning_parts = []
        
        # Risk explanation
        high_risks = [f for f, v in risk_factors.items() if v > 0.6]
        if high_risks:
            reasoning_parts.append(
                f"High risk factors: {', '.join(high_risks)}"
            )
        
        # Benefit explanation
        high_benefits = [f for f, v in benefit_factors.items() if v > 0.6]
        if high_benefits:
            reasoning_parts.append(
                f"High benefit factors: {', '.join(high_benefits)}"
            )
        
        # Confidence explanation
        reasoning_parts.append(
            f"Confidence level: {confidence_level.value.replace('_', ' ')}"
        )
        
        reasoning = ". ".join(reasoning_parts) + "."
        
        # Build recommendation
        if approval_decision == ApprovalDecision.AUTO_APPROVE:
            recommendation = (
                "This change can be applied automatically. "
                "Risk is low and benefits are clear."
            )
        elif approval_decision == ApprovalDecision.AUTO_APPROVE_MONITOR:
            recommendation = (
                "This change can be applied automatically with monitoring. "
                "Post-apply monitoring will detect any issues."
            )
        elif approval_decision == ApprovalDecision.MANUAL_APPROVE:
            recommendation = (
                "Manual approval is recommended. "
                "Risk factors require human review."
            )
        else:  # REJECT
            recommendation = (
                "This change is too risky for autonomous application. "
                "Manual review and approval required."
            )
        
        # Build conditions
        conditions = []
        
        if approval_decision == ApprovalDecision.AUTO_APPROVE_MONITOR:
            conditions.append("Monitor system stability for 5 minutes after apply")
            conditions.append("Rollback automatically if errors detected")
        
        if approval_decision == ApprovalDecision.MANUAL_APPROVE:
            conditions.append("Review all affected files")
            conditions.append("Run manual tests if needed")
            conditions.append("Confirm approval before apply")
        
        if approval_decision == ApprovalDecision.REJECT:
            conditions.append("Do not apply automatically")
            conditions.append("Requires complete manual review")
            conditions.append("Consider alternative approaches")
        
        return reasoning, recommendation, conditions
    
    def record_outcome(
        self,
        decision: Decision,
        success: bool,
        notes: str = ""
    ) -> None:
        """
        Record the outcome of a decision for learning.
        
        Args:
            decision: The decision that was made
            success: Whether the change was successful
            notes: Additional notes about the outcome
        """
        if not self.enable_learning:
            return
        
        outcome = {
            "timestamp": datetime.now().isoformat(),
            "confidence_score": decision.confidence_score,
            "approval_decision": decision.approval_decision.value,
            "requirement_type": decision.requirement.requirement_type.value,
            "complexity": decision.requirement.complexity.value,
            "success": success,
            "notes": notes,
        }
        
        self.historical_data.append(outcome)
        
        # Update success rate (moving average)
        recent_outcomes = self.historical_data[-20:]  # Last 20 decisions
        if recent_outcomes:
            success_count = sum(1 for o in recent_outcomes if o["success"])
            self.historical_success_rate = success_count / len(recent_outcomes)
        
        print(f"[DecisionEngine] Recorded outcome. Success rate: {self.historical_success_rate:.2f}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get decision engine statistics."""
        return {
            "total_decisions": len(self.historical_data),
            "success_rate": self.historical_success_rate,
            "auto_approve_threshold": self.auto_approve_threshold,
            "recent_decisions": self.historical_data[-10:] if self.historical_data else [],
        }
