"""
Unit tests for TriggerDetector.

Tests all valid and invalid examples to ensure the detector correctly
identifies self-improvement requests.
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trigger_detector import TriggerDetector, TriggerType


# Valid examples that MUST trigger
VALID_EXAMPLES = [
    ("Improve yourself.", TriggerType.IMPROVE_SELF),
    ("Improve your own code.", TriggerType.IMPROVE_SELF),
    ("Add a timer feature.", TriggerType.ADD_FEATURE),
    ("Add this feature into yourself.", TriggerType.ADD_FEATURE),
    ("Implement this inside yourself.", TriggerType.ADD_FEATURE),
    ("Modify your own source code.", TriggerType.IMPROVE_SELF),
    ("Self engineer this feature.", TriggerType.GENERAL_IMPROVEMENT),
    ("Add dark mode.", TriggerType.ADD_FEATURE),
    ("Fix your browser automation.", TriggerType.FIX_BUG),
    ("Learn this feature.", TriggerType.LEARN),
    ("Upgrade yourself.", TriggerType.UPGRADE),
    ("Jarvis apne andar timer feature add karo.", TriggerType.ADD_FEATURE),  # Hindi
    ("Jarvis khud ko improve karo.", TriggerType.IMPROVE_SELF),  # Hindi
    ("Jarvis apne code me dark mode implement karo.", TriggerType.ADD_FEATURE),  # Hindi
]

# Invalid examples that should NOT trigger
INVALID_EXAMPLES = [
    "What is the weather today?",
    "Play some music",
    "Open my browser",
    "Send a message to John",
    "Set a reminder for tomorrow",
    "What time is it?",
    "Tell me a joke",
    "Search for pizza recipes",
]


def test_valid_examples():
    """Test that all valid examples trigger correctly."""
    print("\n=== Testing Valid Examples ===")
    detector = TriggerDetector(use_ai=False)  # Disable AI for faster testing
    
    passed = 0
    failed = 0
    
    for text, expected_type in VALID_EXAMPLES:
        trigger, confidence = detector.detect_from_text(text)
        
        if trigger is not None and confidence >= 0.40:
            print(f"[PASS] '{text}' -> {trigger.value} (confidence: {confidence:.2f})")
            passed += 1
        else:
            print(f"[FAIL] '{text}' -> Expected trigger but got {trigger} (confidence: {confidence:.2f})")
            failed += 1
    
    print(f"\nValid Examples: {passed}/{len(VALID_EXAMPLES)} passed")
    return failed == 0


def test_invalid_examples():
    """Test that invalid examples do not trigger."""
    print("\n=== Testing Invalid Examples ===")
    detector = TriggerDetector(use_ai=False)
    
    passed = 0
    failed = 0
    
    for text in INVALID_EXAMPLES:
        trigger, confidence = detector.detect_from_text(text)
        
        if trigger is None or confidence < 0.40:
            print(f"[PASS] '{text}' -> No trigger (confidence: {confidence:.2f})")
            passed += 1
        else:
            print(f"[FAIL] '{text}' -> Unexpected trigger {trigger.value} (confidence: {confidence:.2f})")
            failed += 1
    
    print(f"\nInvalid Examples: {passed}/{len(INVALID_EXAMPLES)} passed")
    return failed == 0


def test_specific_patterns():
    """Test specific pattern matches."""
    print("\n=== Testing Specific Patterns ===")
    detector = TriggerDetector(use_ai=False)
    
    test_cases = [
        ("improve yourself by adding a timer feature", TriggerType.IMPROVE_SELF),
        ("Jarvis improve yourself", TriggerType.IMPROVE_SELF),
        "upgrade yourself to be better",
        "add a new feature for playing music",
        "fix the bug in file_controller",
        "optimize the code for better performance",
        "refactor this module",
        "integrate this project",
    ]
    
    passed = 0
    failed = 0
    
    for text in test_cases:
        trigger, confidence = detector.detect_from_text(text)
        
        if trigger is not None and confidence >= 0.40:
            print(f"✓ PASS: '{text}' -> {trigger.value} (confidence: {confidence:.2f})")
            passed += 1
        else:
            print(f"✗ FAIL: '{text}' -> No trigger (confidence: {confidence:.2f})")
            failed += 1
    
    print(f"\nSpecific Patterns: {passed}/{len(test_cases)} passed")
    return failed == 0


def main():
    """Run all tests."""
    print("=" * 60)
    print("TriggerDetector Unit Tests")
    print("=" * 60)
    
    valid_passed = test_valid_examples()
    invalid_passed = test_invalid_examples()
    specific_passed = test_specific_patterns()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Valid Examples: {'PASS' if valid_passed else 'FAIL'}")
    print(f"Invalid Examples: {'PASS' if invalid_passed else 'FAIL'}")
    print(f"Specific Patterns: {'PASS' if specific_passed else 'FAIL'}")
    
    all_passed = valid_passed and invalid_passed and specific_passed
    print(f"\nOverall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
