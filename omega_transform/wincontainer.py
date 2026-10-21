# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: LGPL-3.0-only
"""Python inside the Windows sandbox: what a process in an AppContainer
(``winsandbox.py``) needs to change about itself before it runs other code.

* ``_ctypes``: the copy that does not need user32.dll
  (``winsandbox.user32_free_ctypes``), named by ``OMEGA_CTYPES``. Processes that
  honour PYTHONPATH find it there anyway; this loads it in those run with ``-I``.
* ``multiprocessing``: its pipes are named pipes under ``\\\\.\\pipe\\``, which a
  container may not create. Under ``\\\\.\\pipe\\LOCAL\\`` they go into the
  container's own namespace, where its other processes find them too.

``setup`` does both, in a process that ``winsandbox.py`` started in a container
(``OMEGA_APPCONTAINER``), and nothing elsewhere. The candidate runner calls it at
the top of its module, which multiprocessing's child processes execute again,
so they are set up as well; other Python processes get it from the
``sitecustomize.py`` next to this file (``winsandbox_site``).

Standard library only, and loadable by path; it must not import ctypes itself.
"""
from __future__ import annotations

import os
import sys

CONTAINER_ENV = "OMEGA_APPCONTAINER"
CTYPES_ENV = "OMEGA_CTYPES"


def use_user32_free_ctypes() -> None:
    path = os.environ.get(CTYPES_ENV)
    if path and "_ctypes" not in sys.modules:
        import importlib.util
        from importlib.machinery import ExtensionFileLoader
        file = os.path.join(path, "_ctypes.pyd")
        spec = importlib.util.spec_from_file_location("_ctypes", file,
                                                      loader=ExtensionFileLoader("_ctypes", file))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["_ctypes"] = module


def _local_pipes(connection) -> None:
    if getattr(connection.arbitrary_address, "omega_local", False):
        return
    original = connection.arbitrary_address

    def arbitrary_address(family):
        address = original(family)
        if family == "AF_PIPE":
            address = address.replace("\\\\.\\pipe\\", "\\\\.\\pipe\\LOCAL\\", 1)
        return address

    arbitrary_address.omega_local = True
    connection.arbitrary_address = arbitrary_address


class _PatchOnImport:
    """Applies ``_local_pipes`` when multiprocessing.connection is first imported:
    importing it right away would add 45 ms to every Python process."""

    def find_spec(self, name, path=None, target=None):
        if name != "multiprocessing.connection":
            return None
        from importlib.machinery import PathFinder
        spec = PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            run = spec.loader.exec_module

            def exec_module(module):
                run(module)
                _local_pipes(module)

            spec.loader.exec_module = exec_module
        return spec


def use_local_pipes() -> None:
    if "multiprocessing.connection" in sys.modules:
        _local_pipes(sys.modules["multiprocessing.connection"])
    elif not any(isinstance(f, _PatchOnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _PatchOnImport())


def setup() -> None:
    if sys.platform == "win32" and os.environ.get(CONTAINER_ENV):
        use_user32_free_ctypes()
        use_local_pipes()
