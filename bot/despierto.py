"""Impide que el equipo se duerma mientras el bot corre (la pantalla sí puede apagarse).

Mac: `caffeinate -i -w <pid>` (se apaga solo si el bot muere). Windows: SetThreadExecutionState.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger("bot.despierto")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class Despierto:
    def __init__(self):
        self._proceso: subprocess.Popen | None = None

    def __enter__(self) -> Despierto:
        if sys.platform == "darwin":
            self._proceso = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log.info("El equipo no se dormirá mientras el bot corra")
        return self

    def __exit__(self, *_exc) -> None:
        if self._proceso is not None:
            self._proceso.terminate()
            self._proceso.wait(timeout=5)
            self._proceso = None
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
