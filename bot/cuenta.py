"""Cuenta de TikTok desde el panel: conectar (abre la ventana de inicio de sesión), «Ya entré» y desconectar.
Así el cliente no necesita la Terminal ni el acceso del Escritorio."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

from bot import config, instalador, navegador
from bot.ritmo import iso

log = logging.getLogger("bot.cuenta")


def conectar() -> bool:
    """Lanza `python -m bot iniciar-sesion` aparte (para el bot, abre Chrome normal con el perfil del bot y,
    cuando se cierra esa ventana, vuelve a arrancar el bot). False si ya había una ventana abierta."""
    if navegador.ventana_sesion_abierta():
        return False
    logs = config.carpeta_datos(crear=True) / "logs"
    logs.mkdir(exist_ok=True)  # en una carpeta de datos nueva aún no existe (daba «Internal Server Error»)
    salida = open(logs / "sesion.log", "a", encoding="utf-8")  # noqa: SIM115
    extra = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} \
        if sys.platform.startswith("win") else {"start_new_session": True}
    subprocess.Popen([sys.executable, "-m", "bot", "iniciar-sesion"], cwd=str(instalador.APP), stdin=subprocess.DEVNULL,
                     stdout=salida, stderr=subprocess.STDOUT, **extra)
    # Se espera a que la ventana esté abierta para que el panel ya muestre «Ya entré».
    for _ in range(40):
        if navegador.ventana_sesion_abierta():
            break
        time.sleep(0.5)
    return True


def listo() -> bool:
    """«Ya entré»: cierra la ventana (guarda la sesión). El proceso de inicio de sesión arranca el bot solo."""
    return navegador.cerrar_ventana_sesion()


def desconectar(conn: sqlite3.Connection, ahora: datetime) -> None:
    """Quita la cuenta: para el bot, aparta el perfil de Chrome (se puede recuperar) y deja la cuenta vacía."""
    servicio = instalador.servicio_instalado("bot")
    if servicio:
        instalador.parar("bot")
    perfil = navegador.carpeta_perfil()
    for _ in range(40):  # el Chrome del bot tarda unos segundos en soltar el perfil
        if not navegador._perfil_en_uso(perfil):
            break
        time.sleep(0.5)
    if perfil.exists():
        perfil.rename(perfil.with_name(f"perfil-desconectado-{ahora:%Y%m%d-%H%M%S}"))
    config.guardar(conn, "cuenta", "")
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'sin_cuenta', mensaje = 'Falta conectar la cuenta de TikTok' WHERE id = 1")
        conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('cuenta', 'cuenta desconectada desde el panel', ?)",
                     (iso(ahora),))
    log.info("Cuenta de TikTok desconectada desde el panel")
    if servicio:
        instalador.arrancar("bot")
