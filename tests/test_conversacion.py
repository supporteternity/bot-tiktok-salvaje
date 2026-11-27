"""Conversación (§17 D37, D41): número, clasificación y decisión por etapa."""

import pytest

from bot import config
from bot.conversacion import clasificar, contiene, decidir, extraer_telefono, palabra_de_descripcion, trae_palabra

CFG = {k: config.DEFAULTS[k] for k in ("palabras_humano", "palabras_interes", "max_respuestas_auto")}


@pytest.mark.parametrize("texto, esperado", [
    ("801 555 1234", "8015551234"),
    ("mi cel es (801) 555-1234 bro", "8015551234"),
    ("+52 1 55 1234 5678", "+5215512345678"),
    ("1-801-555-1234", "+18015551234"),
    ("5512345678", "5512345678"),
    ("+57 300 123 4567", "+573001234567"),
])
def test_telefonos(texto, esperado):
    assert extraer_telefono(texto) == esperado


@pytest.mark.parametrize("texto", [
    "hola", "tengo 25 años", "lo vi a las 10:30", "cuesta 1000 dólares", "0000000000",
    "https://www.tiktok.com/@a/video/7100000000000000002", "https://x.com/?utm_content=7300000000000000000",
    "123 456",
])
def test_no_son_telefonos(texto):
    assert extraer_telefono(texto) is None


@pytest.mark.parametrize("texto, clase", [
    ("8015551234", "telefono"),
    ("¿Cuánto cuesta?", "humano"),
    ("cual es el precio", "humano"),
    ("esto es una estafa", "humano"),
    ("¿y de qué se trata?", "duda"),
    ("no entiendo bien", "duda"),
    ("Me interesa bro", "interes"),
    ("VAMOS 🔥", "interes"),
    ("gracias", "otro"),
])
def test_clasificar(texto, clase):
    assert clasificar(texto, CFG["palabras_humano"], CFG["palabras_interes"]) == clase


def d(etapa="B", mensajes=("hola",), humano=False, auto=0, enviados=(), tel=None):
    return decidir(etapa, humano, auto, set(enviados), tel, list(mensajes), CFG)


def test_numero_confirma_y_pasa_a_persona():
    r = d(mensajes=["ok", "mi número es 801 555 1234"])
    assert (r.respuesta, r.telefono, r.etapa) == ("confirmar_numero", "8015551234", "C")
    assert r.humano


def test_mismo_numero_otra_vez_no_hace_nada():
    assert d(etapa="C", mensajes=["8015551234"], tel="8015551234") == decidir("C", True, 0, set(), "8015551234", ["8015551234"], CFG)
    assert d(etapa="C", mensajes=["8015551234"], tel="8015551234").respuesta is None


def test_numero_nuevo_en_etapa_c_o_d_se_apunta_sin_contestar():
    r = d(etapa="C", humano=True, mensajes=["mejor este 801 555 9999"], tel="8015551234")
    assert r.telefono == "8015559999" and r.respuesta is None
    assert d(etapa="D", mensajes=["8015559999"], tel="8015551234").etapa is None  # D no vuelve a C


def test_precio_pasa_a_persona_sin_contestar():
    r = d(mensajes=["¿Cuánto cuesta el curso?"])
    assert r.respuesta is None and r.humano


def test_ya_atendida_por_persona_no_contesta():
    assert d(humano=True, mensajes=["hola?"]) == d(mensajes=[])
    assert d(etapa="C", mensajes=["gracias"]).respuesta is None


def test_quien_escribe_sin_info_recibe_el_enlace():
    r = d(etapa="A", mensajes=["dame info"])
    assert (r.respuesta, r.etapa) == ("dm", "B")


def test_interes_animo_y_despues_pide_numero():
    assert d(mensajes=["me interesa"]).respuesta == "animo"
    assert d(mensajes=["me interesa"], enviados={"animo"}).respuesta == "pedir_numero"


def test_duda_pide_numero_y_si_sigue_pasa_a_persona():
    assert d(mensajes=["¿y cómo funciona?"]).respuesta == "pedir_numero"
    r = d(mensajes=["¿pero cómo funciona?"], enviados={"pedir_numero"}, auto=1)
    assert r.respuesta is None and r.humano


def test_tope_de_respuestas_automaticas():
    r = d(mensajes=["ok"], auto=3)
    assert r.respuesta is None and r.humano


def test_palabra_clave():
    assert palabra_de_descripcion('Comenta "VENTAS" y te muestro cómo 🔥') == "VENTAS"
    assert palabra_de_descripcion("comenta la palabra «Salvaje» abajo") == "Salvaje"
    assert palabra_de_descripcion("Mira esto") is None
    assert trae_palabra("¡¡ventas!! 🔥🔥", "VENTAS")
    assert trae_palabra("Académia", "ACADEMIA")
    assert not trae_palabra("ventaso", "VENTAS")
    assert contiene("GANAR💰", "ganar")
