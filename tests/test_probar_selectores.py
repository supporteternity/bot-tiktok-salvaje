"""scripts/probar_selectores.py contra el «TikTok falso» (sin red): OK por selector y sin actuar nunca."""

import importlib.util
import random
import sys
from pathlib import Path

import pytest

from bot import instalador as instalador_bot
from bot import navegador
from bot.pantallas.tiktok import PantallasTikTok
from falso_tiktok import Comentario, FalsoTikTok, Persona

hay_chrome = instalador_bot.hay_chrome()  # Mac, Windows (Program Files / LOCALAPPDATA) y Linux
pytestmark = pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")

_spec = importlib.util.spec_from_file_location(
    "probar_selectores", Path(__file__).resolve().parents[1] / "scripts" / "probar_selectores.py")
probar_selectores = importlib.util.module_from_spec(_spec)
sys.modules["probar_selectores"] = probar_selectores
_spec.loader.exec_module(probar_selectores)


def _correr(conn, falso, reel="r1"):
    with navegador.Navegador(visible=False) as nav:
        falso.instalar(nav.contexto)
        pantallas = PantallasTikTok(nav, conn, random.Random(1), carga_ms=5_000, elemento_ms=1_500)
        filas = probar_selectores.probar(pantallas, "ana", reel)
        assert nav.pagina.url == "about:blank"
    return filas


def test_todo_ok_sin_actuar(conn):
    falso = FalsoTikTok(personas={"ana": Persona("ana", "111", "Ana", previos=1)})
    falso.reels["r1"] = [Comentario("c1", "ana", "hola")]
    filas = _correr(conn, falso)
    fallos = [(f.pagina, f.nombre) for f in filas if f.resultado == "FALLA"]
    assert fallos == []
    assert {f.nombre for f in filas if f.resultado == "SIN COMPROBAR"} >= {"chat_boton_enviar", "reel_opcion_fijar"}
    assert falso.enviados == [] and falso.publicados == []
    probar_selectores.imprimir(filas)


def test_selector_que_falta_sale_como_falla(conn):
    falso = FalsoTikTok(personas={"ana": Persona("ana", "111", "Ana", mensajes="cerrados")})
    filas = {f.nombre: f.resultado for f in _correr(conn, falso, reel=None)}
    assert filas["chat_editor"] == "FALLA" and filas["se puede escribir"] == "FALLA"
    assert filas["(todo el reel)"] == "SIN COMPROBAR"


def test_aviso_para_la_prueba(conn):
    falso = FalsoTikTok(sesion=None, personas={"ana": Persona("ana", "111")})
    filas = _correr(conn, falso)
    assert [f.resultado for f in filas] == ["AVISO"] and filas[0].nombre == "sesion_cerrada"
