"""Prueba con Chrome real (sin ventana) y un perfil temporal: abrir, capturar, cerrar y que el perfil persista."""

from pathlib import Path

import pytest

from bot import instalador as instalador_bot
from bot import config, navegador

hay_chrome = instalador_bot.hay_chrome()  # Mac, Windows (Program Files / LOCALAPPDATA) y Linux
pytestmark = pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")


@pytest.fixture
def datos(tmp_path, monkeypatch):
    carpeta = tmp_path / "datos con espacios"
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(carpeta))
    return carpeta


def test_perfil_persiste_entre_aperturas_y_captura(datos):
    cookie = {"name": "prueba_a3", "value": "sigo-dentro", "domain": "example.com", "path": "/",
              "expires": 4102444800, "secure": True, "sameSite": "Lax"}

    with navegador.Navegador(visible=False) as nav:
        nav.contexto.add_cookies([cookie])
        nav.pagina.set_content("<h1>Hola</h1>")
        png, html = nav.captura("prueba a3")
        assert png.is_file() and png.parent == datos / "capturas"
        assert "Hola" in html.read_text(encoding="utf-8")

    assert (datos / "perfil").is_dir()

    with navegador.Navegador(visible=False) as nav:
        nombres = {c["name"]: c["value"] for c in nav.contexto.cookies("https://example.com")}
        assert nombres.get("prueba_a3") == "sigo-dentro"


def test_perfil_ocupado_da_error_claro(datos):
    """Caso real: el bot tiene Chrome abierto y alguien lanza otro proceso con el mismo perfil."""
    import os
    import subprocess
    import sys

    codigo = (
        "from bot import navegador\n"
        "try:\n"
        "    navegador.Navegador(visible=False).abrir()\n"
        "except navegador.PerfilOcupado:\n"
        "    print('OCUPADO')\n"
    )
    raiz = Path(__file__).resolve().parents[1]
    with navegador.Navegador(visible=False):
        r = subprocess.run([sys.executable, "-c", codigo], cwd=raiz, env={**os.environ},
                           capture_output=True, text=True, timeout=120)
    assert "OCUPADO" in r.stdout, r.stdout + r.stderr


def test_esperar_termina_cuando_no_quedan_pestanas(datos):
    """En Mac cerrar la ventana no cierra Chrome: basta con que no quede ninguna pestaña."""
    with navegador.Navegador(visible=False) as nav:
        extra = nav.contexto.new_page()
        nav.pagina.close()
        extra.close()
        nav.esperar_a_que_cierren()  # no se queda colgado


def test_ventana_minimizada_y_el_bot_sigue_funcionando(datos):
    """Ventana real (se minimiza al instante): queda minimizada y se puede navegar, teclear y capturar."""
    with navegador.Navegador(visible=True, minimizada=True) as nav:
        cdp = nav.contexto.new_cdp_session(nav.pagina)
        ventana = cdp.send("Browser.getWindowForTarget")["windowId"]
        estado = lambda: cdp.send("Browser.getWindowBounds", {"windowId": ventana})["bounds"]["windowState"]  # noqa: E731
        assert estado() == "minimized"
        nav.pagina.set_content('<div contenteditable id="e" style="height:40px"></div>')
        nav.pagina.click("#e")
        nav.pagina.keyboard.type("hola 👋")
        assert nav.pagina.inner_text("#e") == "hola 👋"
        png, _ = nav.captura("minimizada")
        assert png.stat().st_size > 0
        assert estado() == "minimized"


def test_sin_ventana_no_intenta_minimizar(datos):
    with navegador.Navegador(visible=False, minimizada=True) as nav:
        nav.pagina.set_content("<p>ok</p>")
        assert nav.pagina.inner_text("p") == "ok"


def test_perfil_libre_si_el_candado_es_de_un_proceso_que_ya_no_existe(tmp_path):
    import os

    from bot import navegador

    assert navegador._perfil_en_uso(tmp_path) is False
    os.symlink("equipo-999999", tmp_path / "SingletonLock")
    assert navegador._perfil_en_uso(tmp_path) is False
    (tmp_path / "SingletonLock").unlink()
    os.symlink(f"equipo-{os.getpid()}", tmp_path / "SingletonLock")
    assert navegador._perfil_en_uso(tmp_path) is True


def test_ventana_de_sesion_con_pid_viejo_cuenta_como_cerrada(datos):
    import os

    assert navegador.ventana_sesion_abierta() is False
    navegador.archivo_pid().write_text("999999")
    assert navegador.ventana_sesion_abierta() is False and navegador.cerrar_ventana_sesion() is False
    navegador.archivo_pid().write_text(str(os.getpid()))
    assert navegador.ventana_sesion_abierta() is True
    navegador.archivo_pid().unlink()
