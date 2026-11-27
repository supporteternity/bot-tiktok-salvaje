"""Instalador (PLAN-OFICIAL G1, D19). Toda la lógica aquí; los .command / .bat solo instalan uv y llaman a esto.

`python -m bot instalar`:
1. Carpeta de datos: bot.db, config.toml y .env a partir de las plantillas (sin pisar lo que ya exista);
   pide la contraseña del panel si falta.
2. Comprueba Chrome; si falta, lo instala con Playwright.
3. Registra dos servicios que arrancan al iniciar sesión y se reinician si mueren: "bot" y "panel".
   Mac: LaunchAgents (launchd). Windows: Programador de tareas.
   Usan la ruta ACTUAL de la carpeta: si se mueve, se vuelve a ejecutar el instalador y se regeneran.
4. Accesos directos en el Escritorio: "Panel del bot" e "Iniciar sesión del bot".
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from bot import config

APP = Path(__file__).resolve().parents[1]
PREFIJO_MAC = "com.bottiktok"
PREFIJO_WIN = "BotTikTok"
URL_PANEL = "http://127.0.0.1:8765"


@dataclass(frozen=True)
class Servicio:
    nombre: str  # bot | panel
    argumentos: tuple[str, ...]  # tras "python"


SERVICIOS = (Servicio("bot", ("-m", "bot")), Servicio("panel", ("-m", "bot", "panel")))


def python_venv(app: Path = APP, sin_consola: bool = False) -> Path:
    if sys.platform == "win32":
        return app / ".venv" / "Scripts" / ("pythonw.exe" if sin_consola else "python.exe")
    return app / ".venv" / "bin" / "python"


# ---------------------------------------------------------------- Mac (launchd)


def ruta_plist(nombre: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PREFIJO_MAC}.{nombre}.plist"


def plist_mac(s: Servicio, app: Path, logs: Path) -> bytes:
    return plistlib.dumps({
        "Label": f"{PREFIJO_MAC}.{s.nombre}",
        "ProgramArguments": [str(python_venv(app)), *s.argumentos],
        "WorkingDirectory": str(app),
        "RunAtLoad": True,  # al iniciar sesión
        "KeepAlive": True,  # se reinicia si muere
        "ThrottleInterval": 30,
        "ProcessType": "Interactive",  # Chrome visible en la sesión del usuario
        "StandardOutPath": str(logs / f"{s.nombre}.salida.log"),
        "StandardErrorPath": str(logs / f"{s.nombre}.errores.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    })


def _launchctl(*args: str, comprobar: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=comprobar)


def _bootstrap(dominio: str, ruta: Path, intentos: int = 10) -> None:
    """launchd tarda un momento en quitar un servicio recién parado: arrancarlo enseguida falla sin avisar.
    Se reintenta hasta ~10 s; si sigue fallando, se lanza el error para que se vea."""
    import time

    for i in range(intentos):
        r = _launchctl("bootstrap", dominio, str(ruta))
        if r.returncode == 0:
            return
        time.sleep(1)
    raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)


def registrar_mac(app: Path, logs: Path) -> list[Path]:
    rutas = []
    dominio = f"gui/{os.getuid()}"
    for s in SERVICIOS:
        ruta = ruta_plist(s.nombre)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        _launchctl("bootout", f"{dominio}/{PREFIJO_MAC}.{s.nombre}")  # si ya estaba (p. ej. otra ruta), fuera
        ruta.write_bytes(plist_mac(s, app, logs))
        _bootstrap(dominio, ruta)
        rutas.append(ruta)
    return rutas


# ---------------------------------------------------------------- Windows (Programador de tareas)


def usuario_windows() -> str:
    dominio, usuario = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dominio}\\{usuario}" if dominio else usuario


def xml_windows(s: Servicio, app: Path, usuario: str | None = None) -> str:
    """Tarea del Programador de tareas.
    - Al iniciar sesión DE ESTE USUARIO (sin usuario, Windows lo toma como «cualquiera» y pide administrador).
    - Además, cada minuto: si el proceso sigue vivo no hace nada (IgnoreNew); si murió (Chrome cerrado, error),
      lo vuelve a lanzar. RestartOnFailure solo cubre que la tarea no llegue a arrancar, no que el programa termine."""
    usuario = usuario if usuario is not None else usuario_windows()
    argumentos = " ".join(f'"{a}"' if " " in a else a for a in s.argumentos)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Bot TikTok: {escape(s.nombre)}</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(usuario)}</UserId></LogonTrigger>
    <TimeTrigger>
      <Enabled>true</Enabled>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Usuario"><UserId>{escape(usuario)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Usuario">
    <Exec>
      <Command>"{escape(str(python_venv(app, sin_consola=True)))}"</Command>
      <Arguments>{escape(argumentos)}</Arguments>
      <WorkingDirectory>{escape(str(app))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


class ErrorTareas(RuntimeError):
    pass


def _schtasks(*args: str, comprobar: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace")
    if comprobar and r.returncode != 0:
        raise ErrorTareas("El Programador de tareas de Windows no dejó registrar el bot: "
                          f"{(r.stderr or r.stdout).strip()}")
    return r


def registrar_windows(app: Path, datos: Path) -> list[Path]:
    rutas = []
    for s in SERVICIOS:
        tarea = f"{PREFIJO_WIN}-{s.nombre}"
        ruta = datos / f"tarea-{s.nombre}.xml"
        ruta.write_text(xml_windows(s, app), encoding="utf-16")
        _schtasks("/End", "/TN", tarea)
        _schtasks("/Create", "/F", "/TN", tarea, "/XML", str(ruta), comprobar=True)
        _schtasks("/Run", "/TN", tarea, comprobar=True)
        rutas.append(ruta)
    return rutas


# ---------------------------------------------------------------- control de servicios (iniciar sesión, actualizar)


def servicio_instalado(nombre: str) -> bool:
    """¿Está instalado el servicio? Con otra carpeta de datos (pruebas, vistas de demostración del panel) siempre
    False: los servicios trabajan con la carpeta de datos normal, así que un panel de prueba nunca para ni arranca
    el bot de verdad (el 25 nov, «Cambiar de cuenta» en una vista de prueba reinició el bot real)."""
    if os.environ.get(config.VAR_CARPETA_DATOS):
        return False
    if sys.platform == "darwin":
        return ruta_plist(nombre).is_file()
    if sys.platform == "win32":
        return subprocess.run(["schtasks", "/Query", "/TN", f"{PREFIJO_WIN}-{nombre}"], capture_output=True).returncode == 0
    return False


def parar(nombre: str) -> None:
    if sys.platform == "darwin":
        _launchctl("bootout", f"gui/{os.getuid()}/{PREFIJO_MAC}.{nombre}")
    elif sys.platform == "win32":
        # Se desactiva además de terminarla: si no, el disparador de cada minuto la volvería a lanzar
        # (p. ej. en mitad de «Iniciar sesión del bot», con el perfil de Chrome ya abierto).
        _schtasks("/Change", "/TN", f"{PREFIJO_WIN}-{nombre}", "/DISABLE")
        _schtasks("/End", "/TN", f"{PREFIJO_WIN}-{nombre}")


def arrancar(nombre: str) -> None:
    if sys.platform == "darwin":
        _bootstrap(f"gui/{os.getuid()}", ruta_plist(nombre))
    elif sys.platform == "win32":
        _schtasks("/Change", "/TN", f"{PREFIJO_WIN}-{nombre}", "/ENABLE")
        _schtasks("/Run", "/TN", f"{PREFIJO_WIN}-{nombre}")


# ---------------------------------------------------------------- datos, Chrome y accesos directos


def preparar_datos(app: Path = APP) -> Path:
    from bot import db

    datos = config.carpeta_datos(crear=True)
    (datos / "logs").mkdir(exist_ok=True)
    if not (datos / "config.toml").exists():
        shutil.copy(app / "config.ejemplo.toml", datos / "config.toml")
    env = datos / ".env"
    if not env.exists():
        shutil.copy(app / ".env.ejemplo", env)
    db.preparar().close()
    return datos


def hay_chrome() -> bool:
    """¿Está el navegador del bot? En Mac, Chrome for Testing de Playwright (ver navegador.chrome_de_pruebas)."""
    if sys.platform == "darwin":
        from bot import navegador

        return navegador.ruta_chrome() is not None
    if sys.platform == "win32":
        candidatos = [os.environ.get(v, "") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        return any((Path(c) / "Google/Chrome/Application/chrome.exe").exists() for c in candidatos if c)
    return shutil.which("google-chrome") is not None


def instalar_chrome() -> None:
    # Mac: Chrome for Testing (app aparte, no toca el Chrome personal, sin permisos de administrador).
    nombre = "chromium" if sys.platform == "darwin" else "chrome"
    subprocess.run([str(python_venv()), "-m", "playwright", "install", nombre], check=True)


def _escritorio_windows() -> Path | None:
    """Carpeta real del Escritorio (OneDrive suele moverla a ...\\OneDrive\\Escritorio)."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID("{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")  # FOLDERID_Desktop
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        ruta = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ruta)) != 0:
            return None
        try:
            return Path(ruta.value) if ruta.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(ruta)
    except Exception:  # noqa: BLE001 — si algo falla, se usa la carpeta de siempre
        return None


def escritorio() -> Path:
    if sys.platform == "win32":
        real = _escritorio_windows()
        if real and real.is_dir():
            return real
    return Path.home() / "Desktop"


def accesos_directos(app: Path) -> list[Path]:
    destino = escritorio()
    destino.mkdir(exist_ok=True)
    creados = []
    if sys.platform == "win32":
        panel = destino / "Panel del bot.url"
        panel.write_text(f"[InternetShortcut]\r\nURL={URL_PANEL}\r\n", encoding="utf-8")
        sesion = destino / "Iniciar sesión del bot.bat"
        # cmd lee los .bat con la página de códigos de la consola: con chcp 65001 (primera línea, solo ASCII)
        # la ruta en UTF-8 se lee bien aunque tenga acentos o ñ (p. ej. C:\Users\Begoña).
        sesion.write_text(f'@echo off\r\nchcp 65001 >nul\r\ncall "{app / "scripts" / "iniciar_sesion.bat"}"\r\n',
                          encoding="utf-8")
    else:
        panel = destino / "Panel del bot.webloc"
        panel.write_bytes(plistlib.dumps({"URL": URL_PANEL}))
        sesion = destino / "Iniciar sesión del bot.command"
        sesion.write_text(f'#!/bin/bash\nexec "{app / "scripts" / "iniciar_sesion.command"}"\n', encoding="utf-8")
        sesion.chmod(0o755)
    creados += [panel, sesion]
    return creados


# ---------------------------------------------------------------- orden principal


def instalar(solo_mostrar: bool = False) -> int:
    app = APP
    datos = config.carpeta_datos()
    if solo_mostrar:
        print(f"Carpeta del bot:   {app}")
        print(f"Carpeta de datos:  {datos}")
        for s in SERVICIOS:
            if sys.platform == "win32":
                print(f"\n--- Tarea {PREFIJO_WIN}-{s.nombre} ---\n{xml_windows(s, app)}")
            else:
                print(f"\n--- {ruta_plist(s.nombre)} ---\n{plist_mac(s, app, datos / 'logs').decode()}")
        return 0

    print("1/4 Preparando la carpeta de datos…")
    datos = preparar_datos(app)
    print("2/4 Comprobando el navegador del bot…")
    if not hay_chrome():
        print("    No está instalado: instalándolo (unos minutos)…")
        try:
            instalar_chrome()
        except subprocess.CalledProcessError:
            if sys.platform == "darwin":
                print("\nNo se pudo descargar el navegador del bot. Comprueba la conexión a internet y vuelve a abrir este instalador.")
            else:
                print("\nNo se pudo instalar Google Chrome automáticamente (en Windows a veces pide permisos de administrador).")
                print("Instálalo a mano desde https://www.google.com/chrome/ y vuelve a abrir este instalador.")
            return 1
    print("3/4 Registrando el arranque automático (bot y panel)…")
    if sys.platform == "darwin":
        registrar_mac(app, datos / "logs")
    elif sys.platform == "win32":
        try:
            registrar_windows(app, datos)
        except ErrorTareas as e:
            print(f"\n{e}")
            print("Prueba a cerrar sesión en Windows, volver a entrar y abrir de nuevo el instalador. "
                  "Si sigue igual, manda una captura de esta ventana al soporte.")
            return 1
    else:
        print("    Sistema no soportado para arranque automático.")
        return 1
    print("4/4 Creando accesos directos en el Escritorio…")
    accesos_directos(app)
    print(f"\nListo. Se abre el panel en tu navegador ({URL_PANEL}).")
    if config.cargar_secretos().get("PANEL_CONTRASENA"):
        print("Ya estaba instalado: entra con tu contraseña de siempre. El bot sigue con tus datos y ajustes.")
        abrir_panel()
        return 0
    print("Ahí creas tu contraseña. Después, en Inicio, verás lo que falta: conectar tu cuenta de TikTok")
    print("(botón «Conectar mi cuenta», con el código QR, una sola vez), los textos y el enlace de tu página.")
    print("El correo de alertas lo configura soporte.")
    print("El bot empieza en MODO DE PRUEBA: hace todo menos enviar. Se cambia en Ajustes.")
    abrir_panel()
    return 0


# ---------------------------------------------------------------- desinstalar


def quitar_servicios() -> None:
    """Para el bot y el panel y quita su arranque automático."""
    if sys.platform == "darwin":
        for s in SERVICIOS:
            _launchctl("bootout", f"gui/{os.getuid()}/{PREFIJO_MAC}.{s.nombre}")
            ruta_plist(s.nombre).unlink(missing_ok=True)
    elif sys.platform == "win32":
        for s in SERVICIOS:
            tarea = f"{PREFIJO_WIN}-{s.nombre}"
            _schtasks("/End", "/TN", tarea)
            _schtasks("/Delete", "/TN", tarea, "/F")


def cerrar_chrome_del_bot(perfil: Path) -> None:
    """Cierra el Chrome que use el perfil del bot (si quedó abierto). El Chrome personal no se toca."""
    if sys.platform == "win32":
        filtro = str(perfil).replace("'", "''")
        orden = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                 f"Where-Object {{ $_.CommandLine -like '*{filtro}*' }} | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        subprocess.run(["powershell", "-NoProfile", "-Command", orden], capture_output=True)
    else:
        # «--»: sin él, macOS toma el patrón (empieza por «--») por una opción y no cierra nada (auditoría 26 nov).
        subprocess.run(["pkill", "-f", "--", f"--user-data-dir={perfil}"], capture_output=True)


def quitar_accesos_directos() -> list[Path]:
    destino = escritorio()
    nombres = (("Panel del bot.url", "Iniciar sesión del bot.bat") if sys.platform == "win32"
               else ("Panel del bot.webloc", "Iniciar sesión del bot.command"))
    quitados = []
    for nombre in nombres:
        ruta = destino / nombre
        if ruta.exists():
            ruta.unlink()
            quitados.append(ruta)
    return quitados


def apartar_datos(datos: Path, ahora: str | None = None) -> Path | None:
    """Renombra la carpeta de datos (nunca la borra). Una instalación nueva empezará vacía;
    para recuperar los datos basta con devolverle su nombre."""
    if not datos.exists():
        return None
    import time

    destino = datos.with_name(f"{datos.name}-desinstalado-{ahora or time.strftime('%Y%m%d-%H%M%S')}")
    datos.rename(destino)
    return destino


def desinstalar(apartar: bool = False) -> int:
    datos = config.carpeta_datos()
    print("1/3 Parando el bot y el panel y quitando el arranque automático…")
    quitar_servicios()
    cerrar_chrome_del_bot(datos / "perfil")
    print("2/3 Quitando los accesos directos del Escritorio…")
    quitar_accesos_directos()
    print("3/3 Tus datos (comentarios, textos, ajustes, sesión de TikTok)…")
    if apartar:
        import time

        time.sleep(2)  # que Chrome y el bot suelten los archivos antes de renombrar
        destino = apartar_datos(datos)
        if destino:
            print(f"    Apartados (no borrados) en: {destino}")
            print(f"    Para recuperarlos, cambia el nombre de esa carpeta a «{datos.name}» antes de instalar.")
        else:
            print("    No había datos.")
    else:
        print(f"    Se quedan donde estaban: {datos}")
        print("    Si vuelves a instalar, el bot sigue con ellos.")
    print("\nListo: el bot está desinstalado y ya no arranca solo.")
    print(f"La carpeta del programa se puede llevar a la papelera: {APP}")
    return 0


def abrir_panel(espera_max: float = 20) -> None:
    """Abre el panel en el navegador en cuanto el servicio del panel responde (acaba de arrancar)."""
    import time
    import urllib.request
    import webbrowser

    limite = time.monotonic() + espera_max
    while time.monotonic() < limite:
        try:
            urllib.request.urlopen(URL_PANEL + "/entrar", timeout=2)
            break
        except OSError:
            time.sleep(1)
    webbrowser.open(URL_PANEL)
