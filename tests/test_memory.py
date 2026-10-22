# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
import unittest
from unittest import mock

from omega_transform import memory

REAL_GPU_FREE_GB = memory.gpu_free_gb


class MemoryGuardTest(unittest.TestCase):
    def setUp(self):
        # The real GPUs of the test machine must not change these figures.
        patcher = mock.patch.object(memory, "gpu_free_gb", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_the_part_beyond_the_gpus_needs_ram(self):
        # 2x 11 GB GPUs, 21.6 GB RAM free: a 17 GB model fits on the GPUs (the case
        # that was refused on the Windows workstation).
        with mock.patch.object(memory, "available_gb", return_value=21.6), \
                mock.patch.object(memory, "gpu_free_gb", return_value=19.2):
            ok, why = memory.fits(int(15.6e9), reserve_gb=6, measured_gb=16.5)
            self.assertTrue(ok, why)
            self.assertIn("on the GPUs", why)
        # Too little GPU memory: the rest has to fit into RAM.
        with mock.patch.object(memory, "available_gb", return_value=10.0), \
                mock.patch.object(memory, "gpu_free_gb", return_value=4.0):
            ok, why = memory.fits(int(15.6e9), reserve_gb=6, measured_gb=16.5)
            self.assertFalse(ok)
            self.assertIn("14.0 GB of RAM", why)

    def test_file_cache_is_released_before_a_model_is_refused(self):
        # Windows holds the file cache of a just-read model in use; once released it is
        # available again, and the model fits.
        free = iter([4.5, 40.0])
        with mock.patch.object(memory, "available_gb", side_effect=lambda: next(free)), \
                mock.patch.object(memory, "release_file_cache", return_value=True) as rel:
            ok, why = memory.fits(int(6.6e9), reserve_gb=6, measured_gb=6.7)
        self.assertTrue(ok, why)
        rel.assert_called_once()
        # Nothing released (not elevated, or not Windows): refused as before.
        with mock.patch.object(memory, "available_gb", return_value=4.5), \
                mock.patch.object(memory, "release_file_cache", return_value=False):
            self.assertFalse(memory.fits(int(6.6e9), reserve_gb=6, measured_gb=6.7)[0])

    def test_gpu_memory_is_read_from_nvidia_smi(self):
        done = mock.Mock(stdout="10422\n10422\n")  # MiB free on each of two GPUs
        with mock.patch.object(memory.sys, "platform", "win32"), \
                mock.patch.object(memory.shutil, "which", return_value="nvidia-smi"), \
                mock.patch.object(memory.subprocess, "run", return_value=done):
            gb = REAL_GPU_FREE_GB()
        self.assertAlmostEqual(gb, 2 * (10422 * 2**20 / 1e9 - memory.GPU_OVERHEAD_GB), places=6)
        with mock.patch.object(memory.sys, "platform", "darwin"):
            self.assertIsNone(REAL_GPU_FREE_GB())  # Apple GPUs share the RAM

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
