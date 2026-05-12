# -*- coding: utf-8 -*-
"""Drift checks for the generated async API module."""

from scripts.generate_async_api import assert_generated_file_current


def test_async_generated_file_matches_dry_run():
    assert_generated_file_current()
