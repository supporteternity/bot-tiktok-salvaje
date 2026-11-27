"""Botones de doble clic: carpeta MAC/ (.command) y carpeta WINDOWS/ (.bat), que llaman a los scripts comunes."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
ACCIONES = {"INSTALAR": "instalar", "INICIAR-SESION": "iniciar_sesion", "ACTUALIZAR": "actualizar",
            "DESINSTALAR": "desinstalar"}
SCRIPTS_WINDOWS = [*sorted((APP / "WINDOWS").glob("*.bat")), *sorted((APP / "scripts").glob("*.bat")),
                   APP / "scripts" / "_lugar_seguro_windows.ps1"]


def test_carpeta_mac_y_carpeta_windows_con_los_mismos_botones():
    assert sorted(p.stem for p in (APP / "MAC").iterdir()) == sorted(ACCIONES)
    assert sorted(p.stem for p in (APP / "WINDOWS").iterdir()) == sorted(ACCIONES)
    for boton, script in ACCIONES.items():
        mac = (APP / "MAC" / f"{boton}.command").read_text(encoding="utf-8")
        win = (APP / "WINDOWS" / f"{boton}.bat").read_text(encoding="ascii")
        nombre_mac = "instalar_mac" if script == "instalar" else script
        nombre_win = "instalar_windows" if script == "instalar" else script
        assert mac.startswith("#!/bin/bash\n") and f'scripts/{nombre_mac}.command"' in mac, boton
        assert f'call "%~dp0..\\scripts\\{nombre_win}.bat"' in win, boton
        assert (APP / "scripts" / f"{nombre_mac}.command").is_file() and (APP / "scripts" / f"{nombre_win}.bat").is_file()


def test_botones_mac_se_pueden_abrir_con_doble_clic():
    for ruta in [*(APP / "MAC").glob("*.command"), APP / "scripts" / "instalar_mac.command"]:
        assert os.access(ruta, os.X_OK), ruta


@pytest.mark.parametrize("ruta", SCRIPTS_WINDOWS, ids=lambda r: r.name)
def test_scripts_windows_en_ascii_y_con_saltos_de_windows(ruta):
    datos = ruta.read_bytes()
    datos.decode("ascii")  # cmd y PowerShell 5 leen mal los acentos
    assert b"\r\n" in datos and datos.count(b"\n") == datos.count(b"\r\n")


def test_windows_avisa_si_no_se_descomprimio_y_copia_a_sitio_seguro():
    for boton in (APP / "WINDOWS").glob("*.bat"):
        assert 'if not exist "%~dp0..\\pyproject.toml" goto sin_descomprimir' in boton.read_text(encoding="ascii")
    for script in ("instalar_windows.bat", "actualizar.bat"):
        assert 'call "%~dp0_lugar_seguro_windows.bat" || goto error' in (APP / "scripts" / script).read_text("ascii")
    ps1 = (APP / "scripts" / "_lugar_seguro_windows.ps1").read_text("ascii")
    for carpeta in ("Downloads", "Desktop", "Documents", "OneDrive", "TEMP"):
        assert carpeta in ps1
    assert r"Programs\Bot TikTok" in (APP / "scripts" / "_lugar_seguro_windows.bat").read_text("ascii")


def test_scripts_mac_sin_errores_de_sintaxis():
    for ruta in (*(APP / "MAC").glob("*.command"), *(APP / "scripts").glob("*.command"),
                 APP / "scripts" / "_lugar_seguro_mac.sh"):
        subprocess.run(["bash", "-n", str(ruta)], check=True)


@pytest.mark.skipif(sys.platform == "win32", reason="script de Mac")
def test_instalar_mac_sin_el_resto_del_bot_avisa_y_para(tmp_path):
    (tmp_path / "MAC").mkdir()
    suelto = tmp_path / "MAC" / "INSTALAR.command"
    shutil.copy(APP / "MAC" / "INSTALAR.command", suelto)
    r = subprocess.run(["bash", str(suelto)], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert r.returncode == 1 and "Descomprime el .zip entero" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="script de Mac")
def test_instalar_mac_copia_a_sitio_seguro_desde_descargas(tmp_path):
    """Con HOME falso: desde «Descargas» se copia a ~/Applications/Bot TikTok y sigue desde la copia.
    uv y curl falsos (fallan) para que no instale nada: se comprueba la copia y el aviso de fallo."""
    casa = tmp_path / "casa con espacios"
    origen = casa / "Downloads" / "BOT-TIKTOK-APP-main"
    (origen / "scripts").mkdir(parents=True)
    shutil.copytree(APP / "MAC", origen / "MAC")
    shutil.copy(APP / "pyproject.toml", origen)
    for nombre in ("instalar_mac.command", "_lugar_seguro_mac.sh"):
        shutil.copy(APP / "scripts" / nombre, origen / "scripts")
    falsos = tmp_path / "bin"
    falsos.mkdir()
    for programa in ("uv", "curl"):
        (falsos / programa).write_text("#!/bin/bash\nexit 7\n")
        (falsos / programa).chmod(0o755)
    entorno = {**os.environ, "HOME": str(casa), "PATH": f"{falsos}:/usr/bin:/bin"}
    r = subprocess.run(["bash", str(origen / "MAC" / "INSTALAR.command")], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, env=entorno)
    copias = list((casa / "Applications" / "Bot TikTok").glob("BOT-TIKTOK-APP-*"))
    assert len(copias) == 1 and (copias[0] / "scripts" / "instalar_mac.command").is_file()
    assert (copias[0] / "MAC" / "INSTALAR.command").is_file()
    assert "carpeta segura" in r.stdout and "Algo ha fallado" in r.stdout and r.returncode == 1
