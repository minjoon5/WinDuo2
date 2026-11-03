"""The reference implementation of WinDuo2's effect logic.

The app itself is native C++ (native/). What is left here is the logic its
core (native/core) was ported from: the lid travel estimate, the settings and
the effect's decisions and geometry. tests/test_core_parity.py drives both
with the same inputs and checks that they agree.
"""
