"""Logs en JSON (una línea por evento) con rotación 10 MB × 5, en `logs/` de la carpeta de datos."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

TAMANO = 10 * 1024 * 1024
COPIAS = 5
_ESTANDAR = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class FormatoJSON(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        datos = {
            "momento": datetime.fromtimestamp(r.created, UTC).isoformat(timespec="seconds"),
            "nivel": r.levelname,
            "origen": r.name,
            "mensaje": r.getMessage(),
        }
        datos.update({k: v for k, v in vars(r).items() if k not in _ESTANDAR})
        if r.exc_info:
            datos["error"] = self.formatException(r.exc_info)
        return json.dumps(datos, ensure_ascii=False, default=str)


def configurar(carpeta: Path, consola: bool = True) -> logging.Logger:
    carpeta.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("bot")
    limpiar()
    log.setLevel(logging.INFO)
    archivo = RotatingFileHandler(carpeta / "bot.log", maxBytes=TAMANO, backupCount=COPIAS, encoding="utf-8")
    archivo.setFormatter(FormatoJSON())
    log.addHandler(archivo)
    if consola:
        pantalla = logging.StreamHandler(sys.stdout)
        pantalla.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        log.addHandler(pantalla)
    return log


def limpiar() -> None:
    log = logging.getLogger("bot")
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
