import plistlib
import sys
from pathlib import Path

import pytest

from bot import config, instalador

APP_CON_ESPACIOS = Path("/Users/cliente/Mis Cosas/BOT TIK TOK/BOT-TIKTOK-APP")


def test_plist_mac_arranca_al_iniciar_sesion_y_se_reinicia(tmp_path):
    datos = plistlib.loads(instalador.plist_mac(instalador.SERVICIOS[0], APP_CON_ESPACIOS, tmp_path / "logs"))
    assert datos["Label"] == "com.bottiktok.bot"
    assert datos["RunAtLoad"] is True and datos["KeepAlive"] is True
    assert datos["ProgramArguments"][0].endswith("/Mis Cosas/BOT TIK TOK/BOT-TIKTOK-APP/.venv/bin/python")
    assert datos["ProgramArguments"][1:] == ["-m", "bot"]
    assert datos["WorkingDirectory"] == str(APP_CON_ESPACIOS)


def test_plist_del_panel():
    datos = plistlib.loads(instalador.plist_mac(instalador.SERVICIOS[1], APP_CON_ESPACIOS, Path("/tmp")))
    assert datos["Label"] == "com.bottiktok.panel" and datos["ProgramArguments"][1:] == ["-m", "bot", "panel"]


def test_mover_la_carpeta_regenera_las_rutas():
    otra = Path("/Volumes/Otro disco/bot")
    a = instalador.plist_mac(instalador.SERVICIOS[0], APP_CON_ESPACIOS, Path("/tmp"))
    b = instalador.plist_mac(instalador.SERVICIOS[0], otra, Path("/tmp"))
    assert str(otra).encode() in b and str(APP_CON_ESPACIOS).encode() not in b and a != b


def test_xml_windows_al_iniciar_sesion_reinicio_y_rutas_entre_comillas():
    xml = instalador.xml_windows(instalador.SERVICIOS[1], Path("C:/Users/Cliente/Mis Cosas/BOT-TIKTOK-APP"))
    assert "<LogonTrigger>" in xml and "<RestartOnFailure>" in xml and "<ExecutionTimeLimit>PT0S" in xml
    assert "<Command>&quot;" not in xml and '<Command>"' in xml  # la ruta va entre comillas
    assert "Mis Cosas" in xml and "<Arguments>-m bot panel</Arguments>" in xml


def test_xml_windows_relanza_cada_minuto_y_solo_para_este_usuario():
    xml = instalador.xml_windows(instalador.SERVICIOS[0], Path("C:/bot"), usuario="EQUIPO\\Begoña")
    # Si el proceso muere (Chrome cerrado, error), el disparador de cada minuto lo vuelve a lanzar;
    # mientras está vivo, IgnoreNew no abre otro.
    assert "<Interval>PT1M</Interval>" in xml and "<TimeTrigger>" in xml and "<Duration>" not in xml
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    assert xml.count("<UserId>EQUIPO\\Begoña</UserId>") == 2  # disparador al iniciar sesión y Principal
    from xml.dom import minidom
    minidom.parseString(xml.replace('encoding="UTF-16"', 'encoding="UTF-8"').encode())  # XML válido


def test_windows_parar_desactiva_la_tarea_y_arrancar_la_reactiva(monkeypatch):
    llamadas = []
    monkeypatch.setattr(instalador.sys, "platform", "win32")
    monkeypatch.setattr(instalador, "_schtasks", lambda *a, comprobar=False: llamadas.append(a))
    instalador.parar("bot")
    instalador.arrancar("bot")
    assert llamadas == [("/Change", "/TN", "BotTikTok-bot", "/DISABLE"), ("/End", "/TN", "BotTikTok-bot"),
                        ("/Change", "/TN", "BotTikTok-bot", "/ENABLE"), ("/Run", "/TN", "BotTikTok-bot")]


def test_windows_error_de_schtasks_se_explica(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "ERROR: Acceso denegado."))
    with pytest.raises(instalador.ErrorTareas, match="Acceso denegado"):
        instalador._schtasks("/Create", comprobar=True)


def test_accesos_directos_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(instalador.sys, "platform", "win32")
    monkeypatch.setattr(instalador, "escritorio", lambda: tmp_path)
    instalador.accesos_directos(Path("C:/Users/Begoña/Mis Cosas/BOT-TIKTOK-APP"))
    assert "URL=http://127.0.0.1:8765" in (tmp_path / "Panel del bot.url").read_text(encoding="utf-8")
    bat = (tmp_path / "Iniciar sesión del bot.bat").read_bytes()
    lineas = bat.split(b"\r\n")
    assert lineas[0] == b"@echo off" and lineas[1] == b"chcp 65001 >nul"  # antes de la ruta con ñ
    assert "Begoña/Mis Cosas/BOT-TIKTOK-APP/scripts/iniciar_sesion.bat\"".encode() in lineas[2]


def test_preparar_datos_copia_plantillas_sin_pisar(tmp_path, monkeypatch):
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(tmp_path / "datos"))
    datos = instalador.preparar_datos()
    assert (datos / "config.toml").is_file() and (datos / ".env").is_file() and (datos / "bot.db").is_file()
    (datos / "config.toml").write_text('cuenta = "academia"\n', encoding="utf-8")
    instalador.preparar_datos()
    assert (datos / "config.toml").read_text(encoding="utf-8") == 'cuenta = "academia"\n'


def test_accesos_directos(tmp_path, monkeypatch):
    monkeypatch.setattr(instalador, "escritorio", lambda: tmp_path)
    creados = instalador.accesos_directos(APP_CON_ESPACIOS)
    assert len(creados) == 2 and all(c.exists() for c in creados)
    if sys.platform != "win32":
        assert plistlib.loads((tmp_path / "Panel del bot.webloc").read_bytes())["URL"] == "http://127.0.0.1:8765"
        assert f'"{APP_CON_ESPACIOS}/scripts/iniciar_sesion.command"' in (tmp_path / "Iniciar sesión del bot.command").read_text()


@pytest.mark.skipif(sys.platform != "darwin", reason="registro con launchd solo en Mac")
def test_registrar_mac_usa_launchctl_y_escribe_plists(tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(instalador, "ruta_plist", lambda n: tmp_path / f"{n}.plist")
    import subprocess

    def falso(*a, comprobar=False):
        llamadas.append(a)
        return subprocess.CompletedProcess(a, 0)

    monkeypatch.setattr(instalador, "_launchctl", falso)
    rutas = instalador.registrar_mac(APP_CON_ESPACIOS, tmp_path)
    assert [r.name for r in rutas] == ["bot.plist", "panel.plist"]
    assert [a[0] for a in llamadas] == ["bootout", "bootstrap", "bootout", "bootstrap"]


def test_solo_mostrar_no_toca_nada(capsys):
    assert instalador.instalar(solo_mostrar=True) == 0
    salida = capsys.readouterr().out
    assert "com.bottiktok.bot" in salida or "BotTikTok-bot" in salida


def test_arrancar_reintenta_si_launchd_aun_no_solto_el_servicio(monkeypatch):
    import subprocess
    import sys
    import time

    if sys.platform != "darwin":
        return
    respuestas = iter([5, 5, 0])
    llamadas = []

    def falso(*a, comprobar=False):
        llamadas.append(a)
        return subprocess.CompletedProcess(a, next(respuestas))

    monkeypatch.setattr(instalador, "_launchctl", falso)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    instalador.arrancar("panel")
    assert len(llamadas) == 3


# ---------------------------------------------------------------- desinstalar


def test_desinstalar_mac_quita_servicios_y_plists(monkeypatch, tmp_path):
    llamadas = []
    monkeypatch.setattr(instalador.sys, "platform", "darwin")
    monkeypatch.setattr(instalador, "_launchctl", lambda *a, comprobar=False: llamadas.append(a))
    monkeypatch.setattr(instalador, "ruta_plist", lambda n: tmp_path / f"com.bottiktok.{n}.plist")
    for n in ("bot", "panel"):
        (tmp_path / f"com.bottiktok.{n}.plist").write_text("x")
    instalador.quitar_servicios()
    assert [a[0] for a in llamadas] == ["bootout", "bootout"]
    assert llamadas[0][1].endswith("/com.bottiktok.bot") and llamadas[1][1].endswith("/com.bottiktok.panel")
    assert not list(tmp_path.iterdir())


def test_desinstalar_windows_borra_las_tareas(monkeypatch):
    llamadas = []
    monkeypatch.setattr(instalador.sys, "platform", "win32")
    monkeypatch.setattr(instalador, "_schtasks", lambda *a, comprobar=False: llamadas.append(a))
    instalador.quitar_servicios()
    assert ("/Delete", "/TN", "BotTikTok-bot", "/F") in llamadas
    assert ("/Delete", "/TN", "BotTikTok-panel", "/F") in llamadas


def test_desinstalar_quita_solo_nuestros_accesos_directos(monkeypatch, tmp_path):
    monkeypatch.setattr(instalador.sys, "platform", "darwin")
    monkeypatch.setattr(instalador, "escritorio", lambda: tmp_path)
    instalador.accesos_directos(APP_CON_ESPACIOS)
    (tmp_path / "foto.png").write_text("del cliente")
    quitados = instalador.quitar_accesos_directos()
    assert len(quitados) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["foto.png"]
    assert instalador.quitar_accesos_directos() == []  # dos veces no falla


def test_apartar_datos_renombra_y_nunca_borra(tmp_path):
    datos = tmp_path / "BotTikTok"
    (datos / "perfil").mkdir(parents=True)
    (datos / "bot.db").write_text("base")
    destino = instalador.apartar_datos(datos, ahora="20261125-160000")
    assert destino == tmp_path / "BotTikTok-desinstalado-20261125-160000"
    assert (destino / "bot.db").read_text() == "base" and not datos.exists()
    assert instalador.apartar_datos(datos) is None  # sin datos, nada que apartar


def test_cerrar_chrome_del_bot_solo_el_de_su_perfil(monkeypatch):
    ordenes = []
    monkeypatch.setattr(instalador.sys, "platform", "darwin")
    monkeypatch.setattr(instalador.subprocess, "run", lambda a, **k: ordenes.append(a))
    instalador.cerrar_chrome_del_bot(Path("/Users/c/Library/Application Support/BotTikTok/perfil"))
    # «--» antes del patrón (auditoría 26 nov): sin él, macOS toma «--user-data-dir=…» por una opción de pkill
    # («illegal option»), no cierra nada y el Chrome del bot sigue abierto con la sesión de TikTok.
    assert ordenes == [["pkill", "-f", "--", "--user-data-dir=/Users/c/Library/Application Support/BotTikTok/perfil"]]


@pytest.mark.skipif(instalador.sys.platform != "darwin", reason="pkill/pgrep de macOS")
def test_la_orden_de_cerrar_chrome_la_entiende_macos():
    """Con el pgrep de verdad (mismo analizador de opciones que pkill, pero sin cerrar nada): el patrón que empieza
    por «--» se acepta. Código 1 = no hay ningún proceso así (bien); 2 = orden mal formada."""
    import subprocess

    r = subprocess.run(["pgrep", "-f", "--", "--user-data-dir=/no/existe/perfil-de-prueba"], capture_output=True)
    assert r.returncode == 1, r.stderr


def test_orden_desinstalar(monkeypatch):
    from bot import __main__ as principal

    visto = {}
    monkeypatch.setattr(instalador, "desinstalar", lambda apartar=False: visto.setdefault("apartar", apartar) and 0 or 0)
    assert principal.main(["desinstalar", "--apartar-datos"]) == 0 and visto["apartar"] is True


def test_orden_clave_soporte_crea_la_clave_del_freno_sin_guardarla_en_claro(tmp_path, monkeypatch, capsys):
    """27 nov: el correo ya no crea la clave de soporte; soporte la crea con esta orden el día de la instalación."""
    from bot import __main__ as principal
    from bot import config

    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(tmp_path / "datos"))
    respuestas = iter(["corta", "corta", "soporte123", "otra", "soporte123", "soporte123"])
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: next(respuestas))
    assert principal.main(["clave-soporte"]) == 0
    assert config.clave_soporte_correcta("soporte123")
    assert "soporte123" not in (config.carpeta_datos() / ".env").read_text()
    salida = capsys.readouterr().out
    assert "al menos 6" in salida and "no coinciden" in salida
