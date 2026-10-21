# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""Set up Python processes in the Windows sandbox (``wincontainer.py``): the
sandbox puts this directory first on their PYTHONPATH. Another sitecustomize,
of the Python installation, still runs afterwards."""
import importlib.machinery
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_load("omega_wincontainer", os.path.join(os.path.dirname(_here), "wincontainer.py")).setup()

_others = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != os.path.normcase(_here)]
_spec = importlib.machinery.PathFinder.find_spec("sitecustomize", _others)
if _spec is not None and _spec.origin and os.path.dirname(_spec.origin) != _here:
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["sitecustomize"] = _module
    _spec.loader.exec_module(_module)
