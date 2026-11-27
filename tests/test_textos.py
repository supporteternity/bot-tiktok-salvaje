import random
from urllib.parse import parse_qs, urlsplit

import pytest

from bot import config, textos


def meter_texto(conn, tipo, contenido, activo=1):
    with conn:
        return conn.execute("INSERT INTO textos (tipo, contenido, activo) VALUES (?, ?, ?)", (tipo, contenido, activo)).lastrowid


def usar(conn, texto_id, tipo_accion="dm"):
    with conn:
        conn.execute(
            "INSERT INTO acciones (tipo, texto_id, resultado, hecho_en) VALUES (?, ?, 'ok', '2026-11-24T00:00:00+00:00')",
            (tipo_accion, texto_id),
        )


# ---------------------------------------------------------------- {nombre}


@pytest.mark.parametrize(
    "plantilla, nombre, esperado",
    [
        ("Hola {nombre}, gracias por comentar", "Juan", "Hola Juan, gracias por comentar"),
        ("Hola {nombre}, gracias por comentar", "", "Hola, gracias por comentar"),
        ("¡Hola {nombre}! Te escribo", None, "¡Hola! Te escribo"),
        ("{nombre}, te escribo por tu comentario", "", "Te escribo por tu comentario"),
        ("Gracias {nombre} 🙌", "", "Gracias 🙌"),
        ("Sin nombre aquí", "Ana", "Sin nombre aquí"),
        ("¡{nombre}! Te dejo la info", "", "Te dejo la info"),
        ("Gracias por comentar ¡{nombre}! Mira esto", "", "Gracias por comentar. Mira esto"),
        ("¡{nombre}! Te dejo la info", "Ana", "¡Ana! Te dejo la info"),
        ("{nombre}: te dejo la info", "", "Te dejo la info"),
    ],
)
def test_rellenar_nombre(plantilla, nombre, esperado):
    assert textos.rellenar(plantilla, nombre=nombre, landing=None) == esperado


@pytest.mark.parametrize(
    "visible, esperado",
    [("Juan Pérez", "Juan"), ("  maría 🔥", "María"), ("🔥🔥", ""), ("user8273645", ""), (None, ""), ("Ana_Lu", "Ana")],
)
def test_nombre_de_pila(visible, esperado):
    assert textos.nombre_de_pila(visible) == esperado


# ---------------------------------------------------------------- {landing}


def test_landing_con_utm_del_reel():
    utm = {"utm_source": "tiktok", "utm_medium": "dm", "utm_campaign": "bot"}
    url = textos.landing_con_utm("https://academia.com/clase?ref=1", utm, reel_id="7300", medio="dm")
    partes = urlsplit(url)
    q = parse_qs(partes.query)
    assert partes.netloc == "academia.com" and partes.path == "/clase"
    assert q == {"ref": ["1"], "utm_source": ["tiktok"], "utm_medium": ["dm"], "utm_campaign": ["bot"], "utm_content": ["7300"]}


def test_landing_en_respuesta_publica_usa_medio_comentario():
    url = textos.landing_con_utm("https://academia.com", {"utm_medium": "dm"}, reel_id="1", medio="comentario")
    assert parse_qs(urlsplit(url).query)["utm_medium"] == ["comentario"]


def test_rellenar_landing():
    assert textos.rellenar("Mira {landing}", nombre="", landing="https://x.com/?a=1") == "Mira https://x.com/?a=1"
    with pytest.raises(textos.SinLanding):
        textos.rellenar("Mira {landing}", nombre="", landing=None)


# ---------------------------------------------------------------- elegir variante


def test_elige_solo_activos_del_tipo_y_no_repite_la_ultima(conn):
    a = meter_texto(conn, "dm", "A")
    b = meter_texto(conn, "dm", "B")
    meter_texto(conn, "dm", "C apagado", activo=0)
    meter_texto(conn, "respuesta", "R")
    usar(conn, a)
    rng = random.Random(1)
    for _ in range(20):
        assert textos.elegir(conn, "dm", rng)["id"] == b


def test_con_una_sola_variante_se_repite(conn):
    a = meter_texto(conn, "dm", "A")
    usar(conn, a)
    assert textos.elegir(conn, "dm", random.Random(0))["id"] == a


def test_sin_variantes_activas_da_error(conn):
    meter_texto(conn, "dm", "apagado", activo=0)
    with pytest.raises(textos.SinTextos):
        textos.elegir(conn, "dm", random.Random(0))


def test_preparar_texto_completo(conn):
    config.guardar(conn, "landing", "https://academia.com")
    tid = meter_texto(conn, "dm", "Hola {nombre}, aquí tienes la clase: {landing}")
    texto_id, final = textos.preparar(conn, "dm", nombre_visible="Carlos M", reel_id="7300", rng=random.Random(0))
    assert texto_id == tid
    assert final.startswith("Hola Carlos, aquí tienes la clase: https://academia.com?")
    assert "utm_content=7300" in final and "utm_medium=dm" in final
    textos.marcar_usado(conn, tid)
    assert conn.execute("SELECT veces_usado FROM textos WHERE id = ?", (tid,)).fetchone()[0] == 1
