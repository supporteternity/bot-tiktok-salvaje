"""Bucle con las piezas reales (Navegador + PantallasTikTok + DetectorTikTok) contra el «TikTok falso».
Sin sesión, el vigía frena en `sesion_cerrada` y avisa, sin abrir el inicio de sesión. Sin red."""

import random
import time
from pathlib import Path

import pytest

from bot import instalador as instalador_bot
from bot import alertas, navegador
from bot.bucle import Bucle
from bot.detector import DetectorTikTok
from bot.pantallas.tiktok import NavegadorCerrado, PantallasTikTok
from conftest import AHORA
from falso_tiktok import FalsoTikTok

hay_chrome = instalador_bot.hay_chrome()  # Mac, Windows (Program Files / LOCALAPPDATA) y Linux
pytestmark = pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")


@pytest.fixture
def piezas(conn):
    falso = FalsoTikTok(sesion=None)
    nav = navegador.Navegador(visible=False).abrir()
    falso.instalar(nav.contexto)
    pantallas = PantallasTikTok(nav, conn, random.Random(1), carga_ms=5_000, elemento_ms=2_000)
    try:
        yield falso, nav, Bucle(conn, pantallas, DetectorTikTok(conn, pantallas), random.Random(1))
    finally:
        nav.cerrar()


def test_sin_sesion_el_vigia_frena_y_avisa_sin_abrir_el_login(piezas, conn, monkeypatch):
    falso, nav, bucle = piezas
    avisos = []
    monkeypatch.setattr(alertas, "enviar_alerta", lambda _c, alerta, _a: avisos.append(alerta) or True)

    bucle.vuelta(AHORA)

    estado = conn.execute("SELECT modo FROM estado_bot").fetchone()["modo"]
    assert estado == "sesion_cerrada"
    evento = conn.execute("SELECT detalle, captura FROM eventos WHERE tipo = 'sesion_cerrada'").fetchone()
    assert evento and Path(evento["captura"]).is_file()
    assert len(avisos) == 1 and "sesi" in avisos[0].asunto.lower()
    assert not any("/login" in u for u in falso.visitas)

    # Siguiente vuelta: parado, nada en TikTok (pestaña en blanco) hasta que una persona lo resuelva.
    visitas = len(falso.visitas)
    bucle.vuelta(AHORA)
    assert nav.pagina.url == "about:blank" and len(falso.visitas) == visitas


def test_si_cierran_chrome_el_proceso_termina_en_vez_de_contar_errores(piezas):
    _falso, nav, bucle = piezas
    nav.pagina.close()
    with pytest.raises(NavegadorCerrado):
        bucle.vuelta(AHORA)


def test_cerrar_chrome_en_pausa_tambien_se_detecta(piezas, conn):
    """En pausa el bot no toca el navegador; aun así, si cierran Chrome, el proceso termina para reabrirlo."""
    from bot import config

    _falso, nav, bucle = piezas
    config.guardar(conn, "orden_pausa", True)
    bucle.vuelta(AHORA)
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()["modo"] == "pausado"
    nav.contexto.new_cdp_session(nav.pagina).send("Browser.close")  # como salir de Chrome (⌘Q), desde fuera
    time.sleep(2)  # el bot durmiendo entre vueltas: Playwright no procesa el aviso de cierre
    with pytest.raises(NavegadorCerrado):
        bucle.vuelta(AHORA)


def test_instalacion_nueva_sin_cuenta_ni_sesion_dice_que_falta_conectarla_y_no_hace_nada(piezas, conn):
    from bot import config

    falso, _nav, bucle = piezas
    config.guardar(conn, "cuenta", "")
    bucle.vuelta(AHORA)
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()["modo"] == "sin_cuenta"
    assert falso.enviados == [] and falso.publicados == []
    assert not any("tiktokstudio" in u for u in falso.visitas)  # no se pone a trabajar


def test_al_iniciar_sesion_la_cuenta_se_conecta_sola(piezas, conn):
    from datetime import timedelta

    from bot import config

    falso, _nav, bucle = piezas
    config.guardar(conn, "cuenta", "")
    bucle.vuelta(AHORA)
    falso.sesion = "academia"  # la persona inicia sesión con «Iniciar sesión del bot»
    bucle.vuelta(AHORA + timedelta(minutes=6))
    assert config.leer(conn, "cuenta") == "academia"
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()["modo"] != "sin_cuenta"


def test_reanudar_sin_haber_iniciado_sesion_vuelve_a_sesion_cerrada_enseguida(piezas, conn, monkeypatch):
    from datetime import timedelta

    from bot.freno import Freno

    _falso, _nav, bucle = piezas
    monkeypatch.setattr(alertas, "enviar_alerta", lambda *_a: True)
    bucle.vuelta(AHORA)
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()["modo"] == "sesion_cerrada"
    Freno(conn).reanudar_manual(AHORA)  # botón «Reanudar» del panel
    bucle.vuelta(AHORA + timedelta(seconds=30))
    bucle.vuelta(AHORA + timedelta(seconds=60))
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()["modo"] == "sesion_cerrada"
