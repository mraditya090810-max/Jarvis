"""
Trigger Detector - Detects self-improvement requests in natural language.

This module analyzes user requests (text or tool calls) to determine if they
indicate a desire for JARVIS to improve itself, add features, fix bugs, or
optimize its code. It uses pattern matching and AI analysis to classify requests.

Key Features:
- Pattern-based detection for common self-improvement phrases
- AI-powered semantic analysis for complex requests
- Multi-language support (works with any language JARVIS understands)
- Integration with tool dispatch system
- Confidence scoring for detection accuracy
"""

import re
from enum import Enum
from typing import Optional, Tuple
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.ai import call_llm_text


class TriggerType(Enum):
    """Types of self-improvement triggers."""
    IMPROVE_SELF = "improve_self"  # "improve yourself", "upgrade yourself"
    ADD_FEATURE = "add_feature"    # "add feature X", "implement Y"
    FIX_BUG = "fix_bug"           # "fix this bug", "fix the error"
    OPTIMIZE = "optimize"         # "optimize this", "make it faster"
    REFACTOR = "refactor"         # "refactor this module"
    LEARN = "learn"               # "learn to do X", "become able to Y"
    INTEGRATE = "integrate"       # "integrate this project", "merge this"
    UPGRADE = "upgrade"           # "upgrade yourself", "improve performance"
    REDUCE_MEMORY = "reduce_memory"  # "reduce memory usage"
    GENERAL_IMPROVEMENT = "general"  # General improvement request


class TriggerDetector:
    """
    Detects self-improvement triggers from user requests.
    
    Uses a two-stage approach:
    1. Fast pattern matching for common phrases
    2. AI-powered semantic analysis for complex/ambiguous requests
    """
    
    # Pattern-based triggers (fast path)
    # Made more flexible with .* to allow words between key phrases
    PATTERNS = {
        TriggerType.IMPROVE_SELF: [
            r"improve\s+.*?yourself",
            r"upgrade\s+.*?yourself",
            r"better\s+.*?yourself",
            r"enhance\s+.*?yourself",
            r"make\s+.*?yourself\s+.*?better",
            r"khud\s+.*?ko\s+.*?improve",  # Hindi: improve yourself
            r"apne\s+.*?andar\s+.*?feature",  # Hindi: add feature inside yourself
        ],
        TriggerType.ADD_FEATURE: [
            r"add\s+.*?(?:a\s+)?feature",
            r"implement\s+.*?(?:a\s+)?(?:new\s+)?feature",
            r"give\s+.*?(?:yourself\s+)?(?:the\s+)?ability\s+to",
            r"enable\s+.*?(?:yourself\s+)?to",
            r"learn\s+.*?to\s+.*?\w+",
            r"become\s+.*?able\s+.*?to",
            r"add\s+.*?dark\s+mode",  # Specific common request
            r"implement\s+.*?dark\s+mode",
        ],
        TriggerType.FIX_BUG: [
            r"fix\s+.*?(?:this\s+)?(?:the\s+)?bug",
            r"fix\s+.*?(?:this\s+)?(?:the\s+)?error",
            r"fix\s+.*?(?:this\s+)?issue",
            r"resolve\s+.*?(?:this\s+)?(?:the\s+)?bug",
            r"debug\s+.*?(?:this\s+)?(?:the\s+)?issue",
        ],
        TriggerType.OPTIMIZE: [
            r"optimize\s+.*?(?:this\s+)?(?:the\s+)?(?:code|module|system)",
            r"make\s+.*?(?:it|this|the\s+system)\s+.*?faster",
            r"improve\s+.*?(?:performance|speed)",
            r"reduce\s+.*?(?:latency|response\s+time)",
        ],
        TriggerType.REFACTOR: [
            r"refactor\s+.*?(?:this\s+)?(?:the\s+)?(?:code|module)",
            r"clean\s+.*?up\s+.*?(?:this\s+)?(?:the\s+)?code",
            r"reorganize\s+.*?(?:this\s+)?(?:the\s+)?code",
            r"restructure\s+.*?(?:this\s+)?(?:the\s+)?code",
        ],
        TriggerType.INTEGRATE: [
            r"integrate\s+.*?(?:this\s+)?(?:the\s+)?project",
            r"merge\s+.*?(?:this\s+)?(?:the\s+)?(?:project|code)",
            r"add\s+.*?(?:this\s+)?(?:the\s+)?(?:project|code)\s+.*?to\s+.*?yourself",
        ],
        TriggerType.UPGRADE: [
            r"upgrade\s+.*?(?:yourself|the\s+system)",
            r"update\s+.*?(?:yourself|the\s+system)",
            r"improve\s+.*?(?:yourself|the\s+system)",
        ],
        TriggerType.REDUCE_MEMORY: [
            r"reduce\s+.*?(?:memory\s+usage|memory)",
            r"use\s+.*?less\s+.*?memory",
            r"decrease\s+.*?memory",
            r"memory\s+.*?optimi[sz]ation",
        ],
    }
    
    # Tool names that might indicate self-improvement
    SELF_ENGINEERING_TOOLS = {
        "self_engineer",
        "code_helper",
        "dev_agent",
        "code_graph",
    }
    
    # Keywords that suggest self-improvement context
    CONTEXT_KEYWORDS = [
        "yourself", "you", "jarvis", "system", "code", "module",
        "improve", "better", "fix", "optimize", "upgrade", "enhance",
        "feature", "ability", "learn", "integrate", "merge",
    ]
    
    def __init__(self, use_ai: bool = True, ai_timeout: int = 30):
        """
        Initialize the trigger detector.
        
        Args:
            use_ai: Whether to use AI for semantic analysis (default: True)
            ai_timeout: Timeout for AI calls in seconds (default: 30)
        """
        self.use_ai = use_ai
        self.ai_timeout = ai_timeout
        
        # Compile regex patterns for performance
        self._compiled_patterns = {}
        for trigger_type, patterns in self.PATTERNS.items():
            self._compiled_patterns[trigger_type] = [
                re.compile(pattern, re.IGNORECASE)
                for pattern in patterns
            ]
    
    def detect_from_text(self, text: str) -> Tuple[Optional[TriggerType], float]:
        """
        Detect if text contains a self-improvement trigger.
        
        Args:
            text: User request text
            
        Returns:
            Tuple of (trigger_type, confidence_score)
            Returns (None, 0.0) if no trigger detected
        """
        print(f"[TriggerDetector] Input: '{text}'")
        
        if not text or not text.strip():
            print(f"[TriggerDetector] Decision: EMPTY - Empty input")
            return None, 0.0
        
        text_lower = text.lower()
        print(f"[TriggerDetector] Normalized: '{text_lower}'")
        
        # Stage 1: Fast pattern matching
        print(f"[TriggerDetector] Stage 1: Pattern matching...")
        for trigger_type, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    print(f"[TriggerDetector] Matched Pattern: {pattern.pattern} for {trigger_type.value}")
                    print(f"[TriggerDetector] Decision: PATTERN_MATCH - Confidence: 0.85")
                    # High confidence for pattern matches
                    return trigger_type, 0.85
        
        print(f"[TriggerDetector] Stage 1: No pattern matched")
        
        # Stage 2: Check for self-engineering tools
        print(f"[TriggerDetector] Stage 2: Tool name check...")
        matched_tools = [tool for tool in self.SELF_ENGINEERING_TOOLS if tool in text_lower]
        if matched_tools:
            print(f"[TriggerDetector] Matched Tools: {matched_tools}")
            print(f"[TriggerDetector] Decision: TOOL_MATCH - Confidence: 0.60")
            # Medium confidence - might be about self-improvement
            return TriggerType.GENERAL_IMPROVEMENT, 0.60
        
        print(f"[TriggerDetector] Stage 2: No tool matched")
        
        # Stage 3: Check for context keywords
        print(f"[TriggerDetector] Stage 3: Keyword check...")
        matched_keywords = [kw for kw in self.CONTEXT_KEYWORDS if kw in text_lower]
        keyword_count = len(matched_keywords)
        print(f"[TriggerDetector] Matched Keywords: {matched_keywords} (count: {keyword_count})")
        if keyword_count >= 2:
            print(f"[TriggerDetector] Decision: KEYWORD_MATCH - Confidence: 0.40")
            # Low confidence - might be self-improvement
            return TriggerType.GENERAL_IMPROVEMENT, 0.40
        
        print(f"[TriggerDetector] Stage 3: Insufficient keywords (need >= 2)")
        
        # Stage 4: AI-powered semantic analysis (if enabled)
        if self.use_ai:
            print(f"[TriggerDetector] Stage 4: AI semantic analysis...")
            return self._ai_detect(text)
        
        print(f"[TriggerDetector] Decision: NO_MATCH - Confidence: 0.0")
        return None, 0.0
    
    def detect_from_tool_call(
        self, 
        tool_name: str, 
        tool_args: dict
    ) -> Tuple[Optional[TriggerType], float]:
        """
        Detect if a tool call indicates self-improvement intent.
        
        Args:
            tool_name: Name of the tool being called
            tool_args: Arguments passed to the tool
            
        Returns:
            Tuple of (trigger_type, confidence_score)
        """
        # Check if it's a self-engineering tool
        if tool_name in self.SELF_ENGINEERING_TOOLS:
            return TriggerType.GENERAL_IMPROVEMENT, 0.75
        
        # Check tool arguments for self-improvement context
        args_text = " ".join(str(v) for v in tool_args.values())
        trigger, confidence = self.detect_from_text(args_text)
        
        # Boost confidence if it's a code-related tool
        if trigger and tool_name in ["code_helper", "dev_agent"]:
            confidence = min(confidence + 0.15, 1.0)
        
        return trigger, confidence
    
    def _ai_detect(self, text: str) -> Tuple[Optional[TriggerType], float]:
        """
        Use AI to detect self-improvement intent.
        
        This is used for complex/ambiguous requests that don't match
        simple patterns.
        
        Args:
            text: User request text
            
        Returns:
            Tuple of (trigger_type, confidence_score)
        """
        try:
            prompt = f"""Classify this user request for an AI assistant.

Request: "{text}"

Is this a request for the AI to improve itself, add features, fix its own bugs,
optimize its code, or otherwise modify its own capabilities?

Answer with one of these exact words:
- YES (if it's about self-improvement)
- NO (if it's about something else)

Answer:"""
            
            response = call_llm_text(
                prompt, 
                timeout=self.ai_timeout, 
                task="trigger_detection"
            ).strip().upper()
            
            if "YES" in response:
                # Try to classify the type
                type_prompt = f"""What type of self-improvement is this?

Request: "{text}"

Choose one:
- improve_self (general self-improvement)
- add_feature (add new capability)
- fix_bug (fix a bug in the system)
- optimize (improve performance/speed)
- refactor (reorganize code)
- integrate (merge external code)

Answer with just the type name:"""
                
                type_response = call_llm_text(
                    type_prompt,
                    timeout=self.ai_timeout,
                    task="trigger_type_classification"
                ).strip().lower()
                
                # Map response to enum
                type_mapping = {
                    "improve_self": TriggerType.IMPROVE_SELF,
                    "add_feature": TriggerType.ADD_FEATURE,
                    "fix_bug": TriggerType.FIX_BUG,
                    "optimize": TriggerType.OPTIMIZE,
                    "refactor": TriggerType.REFACTOR,
                    "integrate": TriggerType.INTEGRATE,
                }
                
                trigger_type = type_mapping.get(type_response, TriggerType.GENERAL_IMPROVEMENT)
                return trigger_type, 0.70  # Medium confidence for AI detection
            
            return None, 0.0
            
        except Exception as e:
            print(f"[TriggerDetector] AI detection failed: {e}")
            # Fall back to pattern matching result (which would be None at this point)
            return None, 0.0
    
    def is_self_improvement_request(
        self, 
        text: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_args: Optional[dict] = None,
        confidence_threshold: float = 0.50
    ) -> bool:
        """
        Convenience method to check if a request is for self-improvement.
        
        Args:
            text: User request text
            tool_name: Tool being called
            tool_args: Tool arguments
            confidence_threshold: Minimum confidence to consider it a trigger
            
        Returns:
            True if this is a self-improvement request above threshold
        """
        trigger, confidence = None, 0.0
        
        if text:
            trigger, confidence = self.detect_from_text(text)
        elif tool_name and tool_args:
            trigger, confidence = self.detect_from_tool_call(tool_name, tool_args)
        
        return trigger is not None and confidence >= confidence_threshold
    
    def extract_requirement(self, text: str, trigger_type: TriggerType) -> str:
        """
        Extract the specific requirement from the trigger text.
        
        For example, from "add the ability to play music", extract "play music".
        
        Args:
            text: User request text
            trigger_type: Detected trigger type
            
        Returns:
            Extracted requirement string
        """
        # Simple extraction based on trigger type
        text_lower = text.lower()
        
        if trigger_type == TriggerType.ADD_FEATURE:
            # Look for "ability to", "feature", "enable to"
            for pattern in [
                r"ability\s+to\s+(.+?)(?:\.|$)",
                r"feature\s+(?:to\s+)?(.+?)(?:\.|$)",
                r"enable\s+(?:yourself\s+)?to\s+(.+?)(?:\.|$)",
                r"learn\s+to\s+(.+?)(?:\.|$)",
            ]:
                match = re.search(pattern, text_lower, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        
        elif trigger_type == TriggerType.FIX_BUG:
            # Look for what needs fixing
            for pattern in [
                r"fix\s+(?:the\s+)?(.+?)(?:\.|$)",
                r"fix\s+(?:this\s+)?(?:the\s+)?(?:bug|error|issue)\s+(?:in\s+)?(.+?)(?:\.|$)",
            ]:
                match = re.search(pattern, text_lower, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        
        elif trigger_type == TriggerType.OPTIMIZE:
            # Look for what to optimize
            for pattern in [
                r"optimize\s+(?:this\s+)?(?:the\s+)?(.+?)(?:\.|$)",
                r"make\s+(.+?)\s+faster(?:\.|$)",
            ]:
                match = re.search(pattern, text_lower, re.IGNORECASE)
                if match:
                    return match.group(1).strip()
        
        # If no pattern matched, return the original text
        return text.strip()
