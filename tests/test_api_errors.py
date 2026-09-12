from core.api_errors import is_project_access_blocked_error, is_invalid_api_key_error


def test_google_live_project_access_denied_is_detected():
    err = "APIError: 1008 None. Your project has been denied access. Please contact support."
    assert is_project_access_blocked_error(err) is True
    assert is_invalid_api_key_error(err) is False


def test_invalid_key_and_network_errors_are_not_misclassified():
    assert is_invalid_api_key_error("API key not valid") is True
    assert is_project_access_blocked_error("TimeoutError: timed out") is False
    assert is_project_access_blocked_error("ConnectionRefusedError: connection refused") is False
