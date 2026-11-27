"""Chrome real (el instalado en el equipo) con un perfil propio del bot en la carpeta de datos.

- Nunca toca el perfil personal del cliente.
- La sesión de TikTok se guarda en ese perfil: se inicia una vez a mano y se conserva.
- Una sola pestaña de trabajo; capturas (PNG + HTML) en `capturas/` para revisar errores a distancia.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import BrowserContext, Error as ErrorPlaywright, Page, Playwright, sync_playwright

from bot import config
from bot.selectores import URL_INICIO_SESION

log = logging.getLogger("bot.navegador")



def chrome_de_pruebas() -> bool:
    """En Mac el bot usa «Google Chrome for Testing» (el Chromium de Playwright): es una app aparte para macOS,
    con su propio icono en el Dock. Con el Chrome normal, macOS juntaba el del bot y el personal en una sola app
    y la persona no podía abrir su Chrome mientras el bot trabajaba (25 nov). En Windows no pasa: Chrome normal."""
    return sys.platform == "darwin"


def canal() -> str:
    # "chromium": Chrome for Testing completo también sin ventana (no el «headless shell»), así el candado
    # del perfil funciona igual y nunca se abre el mismo perfil dos veces.
    return "chromium" if chrome_de_pruebas() else "chrome"


class FaltaNavegador(RuntimeError):
    """No está instalado el navegador que usa el bot."""


class PerfilOcupado(RuntimeError):
    """El perfil del bot ya está abierto en otra ventana de Chrome."""


def carpeta_perfil() -> Path:
    return config.carpeta_datos(crear=True) / "perfil"


def carpeta_capturas() -> Path:
    ruta = config.carpeta_datos(crear=True) / "capturas"
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


class Navegador:
    def __init__(self, visible: bool = True, minimizada: bool = False):
        self.visible = visible
        self.minimizada = minimizada
        self._pw: Playwright | None = None
        self.contexto: BrowserContext | None = None
        self.pagina: Page | None = None

    # ------------------------------------------------------------ abrir / cerrar

    def abrir(self) -> Navegador:
        perfil = carpeta_perfil()
        perfil.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        try:
            self.contexto = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(perfil),
                channel=canal(),
                headless=not self.visible,
                viewport=None,  # tamaño de ventana normal de Chrome
            )
        except ErrorPlaywright as e:
            self._pw.stop()
            self._pw = None
            texto = str(e)
            if "Executable doesn't exist" in texto or "is not found at" in texto or "distribution" in texto.lower():
                raise FaltaNavegador("Falta el navegador del bot. Vuelve a ejecutar el instalador.") from e
            if "ProcessSingleton" in texto or "SingletonLock" in texto or "already in use" in texto or "existing browser session" in texto:
                raise PerfilOcupado(
                    "El perfil del bot ya está abierto en otra ventana de Chrome. Ciérrala y vuelve a intentarlo."
                ) from e
            raise
        self.pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        log.info("Chrome abierto con el perfil del bot")
        if self.visible and self.minimizada:
            self.minimizar()
        return self

    def minimizar(self, intentos: int = 5) -> bool:
        """Minimiza la ventana (no la oculta ni la saca de la pantalla): el bot sigue igual y quien quiera
        mirar la abre desde el Dock / la barra de tareas. Por CDP, igual en Mac y Windows.
        Se comprueba y se reintenta: si se minimiza mientras la ventana aún se está abriendo, a veces vuelve.
        Solo se hace al arrancar: si alguien la abre para mirar, el bot no se la vuelve a esconder.
        Si falla, el bot sigue con la ventana normal (no es motivo para parar)."""
        try:
            cdp = self.contexto.new_cdp_session(self.pagina)
            ventana = cdp.send("Browser.getWindowForTarget")["windowId"]
            try:
                for _ in range(intentos):
                    cdp.send("Browser.setWindowBounds", {"windowId": ventana, "bounds": {"windowState": "minimized"}})
                    self.pagina.wait_for_timeout(1_000)  # la animación de minimizar tarda (Mac)
                    if cdp.send("Browser.getWindowBounds", {"windowId": ventana})["bounds"]["windowState"] == "minimized":
                        log.info("Ventana del bot minimizada")
                        return True
            finally:
                cdp.detach()
        except ErrorPlaywright as e:
            log.warning("No se pudo minimizar la ventana del bot: %s", e)
            return False
        log.warning("La ventana del bot no se quedó minimizada; sigue abierta (el bot funciona igual)")
        return False

    def cerrar(self) -> None:
        try:
            if self.contexto is not None:
                self.contexto.close()
        except ErrorPlaywright:
            pass  # la ventana ya estaba cerrada
        finally:
            self.contexto = None
            self.pagina = None
            if self._pw is not None:
                self._pw.stop()
                self._pw = None
        log.info("Chrome cerrado")

    def __enter__(self) -> Navegador:
        return self.abrir()

    def __exit__(self, *_exc) -> None:
        self.cerrar()

    # ------------------------------------------------------------ utilidades

    def captura(self, nombre: str) -> tuple[Path, Path]:
        """Guarda PNG + HTML de la página actual. Devuelve sus rutas."""
        limpio = re.sub(r"[^\w-]+", "_", nombre).strip("_") or "captura"
        base = carpeta_capturas() / f"{datetime.now():%Y%m%d-%H%M%S}-{limpio}"
        png, html = base.with_suffix(".png"), base.with_suffix(".html")
        self.pagina.screenshot(path=str(png))
        html.write_text(self.pagina.content(), encoding="utf-8")
        return png, html

    def esperar_a_que_cierren(self) -> None:
        """Bloquea hasta que la persona cierre la ventana (todas las pestañas) o salga de Chrome.
        En Mac, cerrar la ventana no cierra Chrome: por eso se espera a que no quede ninguna pestaña."""
        try:
            while self.contexto.pages:
                self.contexto.pages[0].wait_for_event("close", timeout=0)
        except ErrorPlaywright:
            pass  # Chrome se cerró del todo (⌘Q / salir)


def abrir_con_espera(nav: Navegador, espera_perfil_s: float = 0, pausa_s: float = 2) -> Navegador:
    """Abre el navegador; si el perfil está ocupado, reintenta hasta `espera_perfil_s` (el Chrome del servicio
    recién parado puede tardar unos segundos en cerrarse, sobre todo en Windows)."""
    import time

    limite = time.monotonic() + espera_perfil_s
    while True:
        try:
            return nav.abrir()
        except PerfilOcupado:
            if time.monotonic() >= limite:
                raise
            time.sleep(pausa_s)


def ruta_chrome() -> Path | None:
    """El mismo navegador que usa el bot: en Mac, Chrome for Testing de Playwright; si no, Chrome instalado."""
    import os

    if chrome_de_pruebas():
        with sync_playwright() as p:
            ruta = Path(p.chromium.executable_path)
        return ruta if ruta.exists() else None
    if sys.platform.startswith("win"):
        candidatos = [Path(os.environ.get(v, "")) / "Google/Chrome/Application/chrome.exe"
                      for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if os.environ.get(v)]
    else:
        candidatos = [Path("/usr/bin/google-chrome"), Path("/opt/google/chrome/chrome")]
    return next((c for c in candidatos if c.exists()), None)


# Mismo almacén de claves que el Chrome que abre Playwright: así el bot puede leer las cookies de la sesión
# que la persona inicia aquí (con el llavero normal de la Mac quedarían cifradas con otra clave).
ARGS_PERFIL = ["--use-mock-keychain", "--password-store=basic", "--no-first-run", "--no-default-browser-check"]


def _perfil_en_uso(perfil: Path) -> bool:
    """Chrome deja `SingletonLock` → «equipo-pid» mientras usa el perfil. Si ese proceso ya no existe, está libre."""
    import os

    candado = perfil / "SingletonLock"
    if not candado.is_symlink():
        return candado.exists()
    try:
        pid = int(os.readlink(candado).rsplit("-", 1)[1])
        os.kill(pid, 0)
    except (ValueError, IndexError, ProcessLookupError):
        return False
    except (PermissionError, OSError):
        return True
    return True


def iniciar_sesion(espera_perfil_s: float = 0) -> None:
    """Abre un Chrome normal (sin que lo controle el bot) con el perfil del bot en la página de inicio de sesión.
    La persona inicia sesión a mano; al cerrar, la sesión queda en el perfil y el bot la usa.
    (TikTok no deja iniciar sesión en el Chrome controlado por Playwright: «Maximum number of attempts reached».)"""
    import subprocess
    import threading
    import time

    chrome = ruta_chrome()
    if chrome is None:
        raise FaltaNavegador("No encuentro el navegador del bot. Vuelve a ejecutar el instalador.")
    perfil = carpeta_perfil()
    perfil.mkdir(parents=True, exist_ok=True)
    limite = time.monotonic() + espera_perfil_s
    while _perfil_en_uso(perfil):
        if time.monotonic() >= limite:
            raise PerfilOcupado("El perfil del bot ya está abierto en otra ventana de Chrome. Ciérrala y vuelve a intentarlo.")
        time.sleep(1)

    proc = subprocess.Popen([str(chrome), f"--user-data-dir={perfil}", *ARGS_PERFIL, URL_INICIO_SESION],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    archivo_pid().write_text(str(proc.pid), encoding="utf-8")  # el panel lo usa para «Ya entré»
    _traer_al_frente()
    print("Se ha abierto Chrome con el perfil del bot.", flush=True)
    print("1. Inicia sesión en TikTok con el código QR (resuelve tú cualquier código o verificación).", flush=True)
    print("2. Cuando veas tu cuenta, vuelve a esta ventana y pulsa Enter (o cierra ese Chrome con Cmd+Q / Alt+F4).", flush=True)

    listo = threading.Event()
    if sys.stdin is not None and sys.stdin.isatty():
        threading.Thread(target=lambda: (sys.stdin.readline(), listo.set()), daemon=True).start()
    while proc.poll() is None and not listo.is_set():
        time.sleep(0.5)
    try:
        if proc.poll() is None:
            proc.terminate()  # cierre normal de Chrome: guarda las cookies antes de salir
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        archivo_pid().unlink(missing_ok=True)
    print("Listo. La sesión queda guardada en el perfil del bot.")


# ---------------------------------------------------------------- ventana de inicio de sesión (desde el panel)


def archivo_pid() -> Path:
    return config.carpeta_datos(crear=True) / "sesion_chrome.pid"


def _pid_vivo(pid: int) -> bool:
    import os

    if sys.platform.startswith("win"):
        import subprocess

        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ventana_sesion_abierta() -> bool:
    try:
        return _pid_vivo(int(archivo_pid().read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return False


def cerrar_ventana_sesion() -> bool:
    """«Ya entré» en el panel: cierra el Chrome de inicio de sesión con un cierre normal (guarda las cookies)."""
    import os
    import signal
    import subprocess

    try:
        pid = int(archivo_pid().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if not _pid_vivo(pid):
        return False
    if sys.platform.startswith("win"):
        subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)  # sin /F: se cierra como al pulsar X
    else:
        os.kill(pid, signal.SIGTERM)
    return True


def _traer_al_frente() -> None:
    """La ventana la abre un programa en segundo plano (el panel): en Mac se pone delante a propósito."""
    if sys.platform == "darwin":
        import subprocess
        import time

        time.sleep(2)
        subprocess.run(["osascript", "-e", 'tell application id "com.google.chrome.for.testing" to activate'],
                       capture_output=True)
