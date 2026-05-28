"""
Data Quality Validation Tests Template

Write tests that:
1. Pass for clean (baseline) data
2. Fail for corrupted data
3. Test each issue you identified
"""

import pytest
import pandas as pd
import numpy as np

# TODO: Import your validation class
# from check_data_quality import DataQualityValidator


@pytest.fixture
def baseline_data():
    """Load clean baseline data."""
    # TODO: Load your clean baseline dataframe.
    pass


@pytest.fixture
def corrupted_data():
    """Load corrupted data."""
    # TODO: Load your corrupted dataframe.
    pass


@pytest.fixture
def validator(baseline_data):
    """Create validator initialized with baseline."""
    # TODO: Create DataQualityValidator(baseline_data)
    pass


# ============================================================================
# TEST STRUCTURE EXAMPLES
# ============================================================================


class TestBaselineData:
    """Tests that baseline data should pass validation."""

    def test_baseline_passes_validation(self, baseline_data, validator):
        """Baseline data should have no quality issues."""
        # TODO: Implement
        # result = validator.validate(baseline_data)
        # assert result['is_valid'], f"Baseline failed: {result['issues']}"
        pass


class TestDataQualityIssues:
    """Tests that verify each issue is detected."""

    def test_detect_issue_1(self, corrupted_data, validator):
        """Should detect Issue 1 (TODO: describe your issue)."""
        # TODO: Implement
        # result = validator.validate(corrupted_data)
        # assert not result['is_valid']
        # assert any(issue['type'] == '...' for issue in result['issues'])
        pass

    def test_detect_issue_2(self, corrupted_data, validator):
        """Should detect Issue 2 (TODO: describe your issue)."""
        # TODO: Implement
        pass

    # It's recommended but optional to find all 4 issues:
    # def test_detect_issue_3(self, corrupted_data, validator):
    #     """Should detect Issue 3 (TODO: describe your issue)."""
    #     # TODO: Implement
    #     pass

    # def test_detect_issue_4(self, corrupted_data, validator):
    #     """Should detect Issue 4 (TODO: describe your issue)."""
    #     # TODO: Implement
    #     pass


class TestGracefulDegradation:
    """Tests that API gracefully handles bad data."""

    def test_api_does_not_crash_with_bad_data(self, corrupted_data):
        """API should continue running even with corrupted data."""
        # TODO: Test that your data.py doesn't crash
        # - Load corrupted data
        # - Try to make predictions
        # - Verify API returns something (even if degraded)
        pass

    def test_fallback_is_logged(self, corrupted_data):
        """When graceful degradation happens, it should be logged."""
        # TODO: Test logging
        # - Run with corrupted data
        # - Check logs show what degraded
        pass


# ============================================================================
# HOW TO RUN
# ============================================================================
#
# From repo root:
#   python -m pytest week3/validation/test_data_quality.py -v
#
# To run specific test:
#   python -m pytest week3/validation/test_data_quality.py::TestDataQualityIssues::test_detect_issue_1 -v
#
# To see print statements:
#   python -m pytest week3/validation/test_data_quality.py -v -s
