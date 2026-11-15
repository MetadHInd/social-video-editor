"""Fixture for tests that render tiny synthetic videos but do not test the hardware encoder (that is
tests/test_hwaccel.py): software x264 is faster than a GPU session for clips of a few seconds, and
steady when the GPU is busy. Import it into a test module to apply it to every test there:

    from software_encoder import software_encoder  # noqa: F401
"""
from __future__ import annotations

import pytest

from social_video_editor.adapters.system import hwaccel


@pytest.fixture(autouse=True)
def software_encoder(monkeypatch):
    monkeypatch.setenv(hwaccel.SETTING_ENV, "off")
    hwaccel.reset()
    yield
    hwaccel.reset()                 # the next test detects again, with its own environment
