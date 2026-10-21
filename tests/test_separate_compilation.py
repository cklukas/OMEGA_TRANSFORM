# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
"""C/C++ candidates are compiled on their own and checked before linking, so they
cannot reach into the harness that times and checks them (examples/sort_u32_cpp)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from omega_transform.config import load_config
from omega_transform.evaluator import Evaluator

ROOT = Path(__file__).resolve().parent.parent
SORT = ROOT / "examples" / "sort_u32_cpp"


class SymbolFilterTest(unittest.TestCase):
    """Real ``nm -g`` output of candidate objects on macOS, Linux and Windows."""

    def test_windows_object(self):
        from omega_transform.evaluator import filter_symbols_coff
        out = ("00000000 T sort_u32\n"
               "00000000 T ??1?$vector@IV?$allocator@I@std@@@std@@QEAA@XZ\n"
               "         U ??2@YAPEAX_K@Z\n"
               "00000000 R __real@3ff0000000000000\n"
               "00000000 D _Avx2WmemEnabledWeakValue\n"
               "00000000 T printf\n"
               "00000000 T __local_stdio_printf_options\n"
               "00000000 T QueryPerformanceCounter\n"
               "00000000 T helper\n"
               "00000000 T ??2@YAPEAX_K@Z\n")
        self.assertEqual(filter_symbols_coff(out, ["sort_u32"]),
                         ["??2@YAPEAX_K@Z", "QueryPerformanceCounter", "helper"])

    def test_linux_object(self):
        from omega_transform.evaluator import filter_symbols
        out = ("0000000000000000 V DW.ref.__gxx_personality_v0\n"
               "                 U __gxx_personality_v0\n"
               "0000000000000000 T sort_u32\n"
               "0000000000000000 W _ZNSt6vectorIjSaIjEED2Ev\n"
               "0000000000000000 W __clang_call_terminate\n"
               "0000000000000010 T clock_gettime\n"
               "0000000000000020 W __stack_chk_fail\n"
               "0000000000000030 T _Znwm\n")
        self.assertEqual(filter_symbols(out, ["sort_u32"], darwin=False),
                         ["_Znwm", "__stack_chk_fail", "clock_gettime"])

    def test_macos_object(self):
        from omega_transform.evaluator import filter_symbols
        out = ("0000000000000000 T _sort_u32\n"
               "                 U ___gxx_personality_v0\n"
               "0000000000000040 T _helper\n")
        self.assertEqual(filter_symbols(out, ["sort_u32"], darwin=True), ["helper"])


@unittest.skipUnless(shutil.which("clang++"), "needs clang++")
class SeparateCompilationTest(unittest.TestCase):
    def evaluate(self, injected: str, at_end: str = ""):
        cfg = load_config(SORT)
        cfg.evaluator.stages = cfg.evaluator.stages[:1]  # the fast correctness stage suffices
        code = cfg.initial_path.read_text(encoding="utf-8")
        marker = next(ln for ln in code.splitlines(keepends=True) if "OMEGA-BLOCK-START" in ln)
        code = code.replace(marker, marker + injected + "\n", 1)
        if at_end:
            end = next(ln for ln in code.splitlines(keepends=True) if "OMEGA-BLOCK-END" in ln)
            code = code.replace(end, at_end + "\n" + end, 1)
        with tempfile.TemporaryDirectory() as tmp:
            return Evaluator(cfg, Path(tmp)).evaluate(code, "t")

    def test_unchanged_candidate_passes(self):
        self.assertEqual(self.evaluate("").status, "ok")

    def test_replacing_a_library_function_is_refused(self):
        r = self.evaluate('#include <time.h>\nextern "C" int clock_gettime(clockid_t, '
                          'struct timespec* t) { t->tv_sec = 0; t->tv_nsec = 0; return 0; }')
        self.assertEqual(r.status, "compile_error")
        self.assertIn("clock_gettime", r.feedback)

    def test_extra_global_c_symbol_is_refused(self):
        r = self.evaluate('extern "C" void helper(void) {}')
        self.assertEqual(r.status, "compile_error")
        self.assertIn("helper", r.feedback)

    def test_macros_do_not_reach_the_harness(self):
        # In a single file this would have silenced the harness's JSON output.
        self.assertEqual(self.evaluate("#define printf(...) 0").status, "ok")

    def test_harness_internals_are_out_of_reach(self):
        r = self.evaluate("static double cpu_seconds();",
                          at_end="static const double USED = cpu_seconds();")
        self.assertEqual(r.status, "compile_error")

    def test_compiled_program_cannot_read_the_problem_directory(self):
        # If the harness source were readable, the program would corrupt its output.
        r = self.evaluate(
            "#include <cstdio>\n#include <cstdlib>\n#include <string>\n"
            "static bool can_read() { const char* d = std::getenv(\"OMEGA_PROBLEM_DIR\");\n"
            "  if (!d) return false; std::FILE* f = std::fopen((std::string(d) + "
            "\"/harness.cpp\").c_str(), \"r\");\n  if (f) std::fclose(f); return f != nullptr; }\n"
            "static const bool READABLE = can_read();",
            at_end='static struct Poison { Poison() { if (READABLE) std::abort(); } } poison;')
        self.assertEqual(r.status, "ok", r.feedback)


if __name__ == "__main__":
    unittest.main()
