# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import unittest
from unittest import mock

from omega_transform import memory


class MemoryGuardTest(unittest.TestCase):
    def test_refuses_model_that_does_not_fit(self):
        with mock.patch.object(memory, "available_gb", return_value=20.0):
            ok, why = memory.fits(int(18.6e9), reserve_gb=6)
            self.assertFalse(ok)
            self.assertIn("only 20.0 GB are free", why)
            self.assertTrue(memory.fits(int(6.6e9), reserve_gb=6)[0])

    def test_memory_of_model_to_be_unloaded_counts_as_free(self):
        with mock.patch.object(memory, "available_gb", return_value=10.0):
            self.assertFalse(memory.fits(int(6.6e9), reserve_gb=6)[0])
            self.assertTrue(memory.fits(int(6.6e9), reserve_gb=6, already_loaded_gb=7)[0])

    def test_measured_size_replaces_estimate(self):
        with mock.patch.object(memory, "available_gb", return_value=12.1):
            self.assertFalse(memory.fits(int(6.6e9), reserve_gb=4)[0])  # estimate 8.3 GB
            self.assertTrue(memory.fits(int(6.6e9), reserve_gb=4, measured_gb=6.7)[0])

    def test_waiting_for_memory(self):
        free = iter([10.0, 10.0, 14.0])  # memory frees up on the third look
        with mock.patch.object(memory, "available_gb", side_effect=lambda: next(free)):
            ok, _ = memory.wait_until_fits(int(6.6e9), reserve_gb=4, measured_gb=6.7,
                                           minutes=1, poll_seconds=0.01)
        self.assertTrue(ok)
        with mock.patch.object(memory, "available_gb", return_value=5.0):
            ok, why = memory.wait_until_fits(int(6.6e9), reserve_gb=4, minutes=0.001,
                                             poll_seconds=0.01)
        self.assertFalse(ok)
        self.assertIn("after waiting", why)

    def test_guard_can_be_switched_off(self):
        with mock.patch.object(memory, "available_gb", return_value=1.0), \
                mock.patch.dict("os.environ", {"OMEGA_NO_MEMORY_GUARD": "1"}):
            ok, why = memory.fits(int(18e9), reserve_gb=6)
        self.assertTrue(ok)
        self.assertIn("memory guard off", why)

    def test_estimate_matches_measured_load(self):
        # 17.4 GB of weights measured at 18.4 GB loaded; the estimate must not be lower.
        self.assertGreaterEqual(memory.estimate_model_gb(int(17.4e9)), 18.4)

    def test_available_memory_is_readable_here(self):
        avail = memory.available_gb()
        self.assertIsNotNone(avail)
        self.assertGreater(avail, 0)


if __name__ == "__main__":
    unittest.main()
