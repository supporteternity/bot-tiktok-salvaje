"""Actualizar y volver atrás (PLAN-OFICIAL D18, G3).

La versión nueva se copia (zip por AnyDesk) en una carpeta NUEVA al lado de la actual y se ejecuta
`scripts/actualizar.*` desde ella:
1. Copia de seguridad de bot.db en `copias/` (carpeta de datos).
2. Para el bot y el panel (antes, si falta, descarga Chrome: si no hay red no se para nada).
3. Migra la base a la versión nueva.
4. Apunta el arranque automático a esta carpeta y vuelve a arrancar.
La carpeta anterior queda intacta. Para volver atrás: desde la carpeta anterior,
`scripts/actualizar.* --restaurar <copia>` (devuelve la base a como estaba y apunta el arranque a esa carpeta).
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from bot import config, db, instalador


def carpeta_copias() -> Path:
    ruta = config.carpeta_datos(crear=True) / "copias"
    ruta.mkdir(exist_ok=True)
    return ruta


def copia_seguridad(motivo: str = "actualizar") -> Path | None:
    """Copia consistente de bot.db (API de copia de SQLite, válida aunque haya escrituras)."""
    origen = db.ruta_db()
    if not origen.is_file():
        return None
    destino = carpeta_copias() / f"bot-{datetime.now():%Y%m%d-%H%M%S}-{motivo}.db"
    _copiar_sqlite(origen, destino)
    return destino


def _copiar_sqlite(origen: Path, destino: Path) -> None:
    src, dst = sqlite3.connect(origen), sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def _parar_servicios() -> None:
    for s in instalador.SERVICIOS:
        instalador.parar(s.nombre)


def _arrancar_servicios() -> None:
    """Vuelve a arrancar el bot y el panel tal como estaban registrados (la versión anterior si no se llegó a
    apuntar a esta carpeta). En Windows además reactiva las tareas que `parar` desactivó. Nunca lanza error:
    es lo que se hace cuando algo ya ha fallado."""
    for s in instalador.SERVICIOS:
        try:
            instalador.arrancar(s.nombre)
        except Exception as e:  # noqa: BLE001
            print(f"    No se pudo volver a arrancar «{s.nombre}»: {e}")


def _fallo(paso: str, error: Exception) -> None:
    print(f"\nERROR al {paso}: {error}")


def _registrar_aqui() -> None:
    datos = config.carpeta_datos()
    if sys.platform == "darwin":
        instalador.registrar_mac(instalador.APP, datos / "logs")
    elif sys.platform == "win32":
        instalador.registrar_windows(instalador.APP, datos)
    instalador.accesos_directos(instalador.APP)


def actualizar() -> int:
    """Auditoría 26 nov (hallazgo 9): una actualización que falla a medias no deja el bot apagado. Lo que puede
    fallar sin red (descargar Chrome) va ANTES de parar nada; si después falla la base o el arranque automático, la
    base vuelve a como estaba y se arranca otra vez lo que había."""
    if not instalador.hay_chrome():
        print("0/4 Descargando el navegador del bot (unos minutos)…")
        try:
            instalador.instalar_chrome()
        except Exception as e:  # noqa: BLE001 — sin internet, Playwright que pide otro Chrome…
            _fallo("descargar el navegador del bot", e)
            print("No se ha tocado nada: el bot sigue funcionando con la versión que tenía. "
                  "Revisa la conexión a internet y vuelve a ejecutar la actualización.")
            return 1
    print("1/4 Copia de seguridad de la base de datos…")
    copia = copia_seguridad()
    print(f"    {copia or 'no había base todavía'}")
    print("2/4 Parando el bot y el panel…")
    _parar_servicios()
    print("3/4 Actualizando la base de datos…")
    try:
        db.preparar().close()
    except Exception as e:  # noqa: BLE001
        _fallo("actualizar la base de datos", e)
        if copia:
            _copiar_sqlite(copia, db.ruta_db())  # la versión anterior no abre una base a medio migrar
            print("    La base de datos ha vuelto a como estaba antes de actualizar.")
        _arrancar_servicios()
        print("Se ha vuelto a arrancar la versión anterior: el bot sigue funcionando como antes. Avisa a soporte.")
        return 1
    print("4/4 Apuntando el arranque automático a esta carpeta y arrancando…")
    try:
        _registrar_aqui()
    except Exception as e:  # noqa: BLE001
        _fallo("apuntar el arranque automático a esta carpeta", e)
        _arrancar_servicios()
        print("Se ha intentado arrancar otra vez el bot y el panel. Comprueba que el panel abre y avisa a soporte.")
        return 1
    print(f"\nActualizado. Carpeta en uso: {instalador.APP}")
    if copia:
        print("Para volver a la versión anterior, ejecuta desde la carpeta ANTERIOR:")
        script = r"scripts\actualizar.bat" if sys.platform == "win32" else "scripts/actualizar.command"
        print(f'    {script} --restaurar "{copia}"')
    return 0


def restaurar(copia: Path) -> int:
    if not copia.is_file():
        print(f"No existe la copia {copia}")
        return 1
    print("1/3 Parando el bot y el panel…")
    _parar_servicios()
    print("2/3 Restaurando la base de datos…")
    try:
        seguridad = copia_seguridad("antes-de-restaurar")
        _copiar_sqlite(copia, db.ruta_db())  # sobre la base viva, con la API de SQLite (respeta el modo WAL)
        db.preparar().close()
        print(f"    (la base que había se guardó en {seguridad})")
        print("3/3 Apuntando el arranque automático a esta carpeta y arrancando…")
        _registrar_aqui()
    except Exception as e:  # noqa: BLE001 — como al actualizar: nunca dejar el bot apagado sin avisar
        _fallo("volver a la versión anterior", e)
        _arrancar_servicios()
        print("Se ha intentado arrancar otra vez el bot y el panel. Comprueba que el panel abre y avisa a soporte.")
        return 1
    print(f"\nRestaurado. Carpeta en uso: {instalador.APP}")
    return 0
