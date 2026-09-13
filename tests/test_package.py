"""Smoke tests for the application package."""

import media_recommender


def test_package_imports() -> None:
    """Verify that the application package is importable."""
    assert media_recommender.__doc__
