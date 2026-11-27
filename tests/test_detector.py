"""Detector (C1, C2): JSON simulados → base de datos sin duplicados; histórico; ID estable; repaso por reel.
Los dos últimos tests usan Chrome contra el «TikTok falso» (sin red)."""

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bot import instalador as instalador_bot
from bot import config, detector, navegador
from bot.pantallas.reel import es_lista_comentarios, leer_api_comentarios
from bot.pantallas.tiktok import PantallasTikTok
from conftest import AHORA, iso
from falso_tiktok import Comentario, FalsoTikTok

T0 = int((AHORA - timedelta(days=1)).timestamp())  # comentarios de ayer (después de inicio_desde)


def api_reel(n, desde=0, reel="r1", t=T0, has_more=0):
    return {"status_code": 0, "has_more": has_more, "cursor": desde + n, "comments": [
        {"cid": f"c{i}", "text": f"info {i}", "create_time": t + i, "aweme_id": reel,
         "user": {"uid": f"id{i}", "unique_id": f"user{i}", "nickname": f"Persona {i}"}}
        for i in range(desde, desde + n)]}


def leer(datos, reel=None):
    return leer_api_comentarios(datos, reel)[0]


# ------------------------------------------------------------------ lectura del JSON


def test_lee_formato_de_la_web_y_variante_de_studio():
    c = leer(api_reel(1))[0]
    assert (c.id, c.reel_id, c.usuario, c.user_id, c.nombre, c.texto) == ("c0", "r1", "user0", "id0", "Persona 0", "info 0")
    assert c.comentado_en == datetime.fromtimestamp(T0, UTC)

    studio = {"data": {"has_more": True, "comment_list": [
        {"comment_id": 9, "content": "hola", "create_time": T0, "item_id": 77, "user_info": {"user_id": 5, "unique_id": "@ana"}}]}}
    lote, hay_mas = leer_api_comentarios(studio)
    assert hay_mas and (lote[0].id, lote[0].reel_id, lote[0].usuario, lote[0].user_id) == ("9", "77", "ana", "5")
    assert es_lista_comentarios(studio) and es_lista_comentarios({"comments": []}) and not es_lista_comentarios({"x": 1})


def test_entradas_incompletas_se_ignoran():
    datos = {"comments": [{"cid": "1", "text": "sin usuario", "create_time": T0, "aweme_id": "r"},
                          {"cid": "2", "user": {"unique_id": "a"}, "aweme_id": "r"},  # sin hora
                          "basura"]}
    assert leer(datos) == []


# ------------------------------------------------------------------ guardar


def test_20_comentarios_20_filas_y_releer_0(conn):
    assert detector.guardar(conn, leer(api_reel(20)), AHORA) == 20
    assert detector.guardar(conn, leer(api_reel(20)), AHORA) == 0
    assert conn.execute("SELECT COUNT(*) FROM comentarios WHERE estado = 'en_cola'").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM contactos").fetchone()[0] == 20
    reel = conn.execute("SELECT * FROM reels").fetchone()
    assert (reel["id"], reel["url"]) == ("r1", "https://www.tiktok.com/@academia/video/r1")


def test_lo_anterior_a_inicio_desde_es_historico(conn):
    viejo = int((AHORA - timedelta(days=60)).timestamp())
    detector.guardar(conn, leer(api_reel(2, t=viejo)) + leer(api_reel(1, desde=5)), AHORA)
    estados = dict(conn.execute("SELECT id, estado FROM comentarios"))
    assert estados == {"c0": "historico", "c1": "historico", "c5": "en_cola"}


def test_misma_persona_en_varios_reels_un_solo_contacto(conn):
    a = api_reel(1, reel="r1")
    b = api_reel(1, reel="r2")
    b["comments"][0]["cid"] = "otro"
    detector.guardar(conn, leer(a) + leer(b), AHORA)
    assert conn.execute("SELECT COUNT(*) FROM contactos").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM reels").fetchone()[0] == 2


def test_cambio_de_usuario_mantiene_el_contacto(conn):
    detector.guardar(conn, leer(api_reel(1)), AHORA)
    conn.execute("UPDATE contactos SET contactado = 1 WHERE user_id = 'id0'")
    nuevo = api_reel(1)
    nuevo["comments"][0].update(cid="c99")
    nuevo["comments"][0]["user"]["unique_id"] = "nombre_nuevo"
    detector.guardar(conn, leer(nuevo), AHORA)
    fila = conn.execute("SELECT usuario, contactado FROM contactos WHERE user_id = 'id0'").fetchone()
    assert (fila["usuario"], fila["contactado"]) == ("nombre_nuevo", 1)


def test_contacto_provisional_pasa_al_id_estable_sin_perder_que_fue_atendido(conn):
    sin_id = api_reel(1)
    del sin_id["comments"][0]["user"]["uid"]
    detector.guardar(conn, leer(sin_id), AHORA)
    conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado' WHERE user_id = 'u:user0'")
    otro = api_reel(1)
    otro["comments"][0]["cid"] = "c7"
    detector.guardar(conn, leer(otro), AHORA)
    filas = conn.execute("SELECT user_id, contactado, resultado FROM contactos").fetchall()
    assert [tuple(f) for f in filas] == [("id0", 1, "dm_enviado")]
    assert {f[0] for f in conn.execute("SELECT user_id FROM comentarios")} == {"id0"}


def test_lead_de_la_bandeja_que_luego_comenta_conserva_etapa_numero_y_conversacion(conn):
    """Auditoría 26 nov: la bandeja crea a la persona como 'u:usuario'. Si dio su número (etapa C, la atiende el
    equipo) y después comenta, al pasar al ID estable no puede volver a la etapa A sin número: el bot le mandaría
    el enlace otra vez y se metería en la conversación del equipo. Sus leads, mensajes y envíos van con ella."""
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, nombre, contactado, resultado, contactado_en, etapa, "
                     "telefono, humano, humano_motivo, respuestas_auto, origen, reel_origen) VALUES "
                     "('u:user0', 'user0', 'Persona 0', 1, 'dm_enviado', ?, 'C', '8015551234', 1, 'dio su número', 2, "
                     "'mensaje', NULL)", (iso(AHORA),))
        conn.execute("INSERT INTO leads (user_id, usuario, telefono, creado_en) VALUES ('u:user0', 'user0', '8015551234', ?)",
                     (iso(AHORA),))
        conn.execute("INSERT INTO mensajes (user_id, de, texto, creado_en) VALUES ('u:user0', 'persona', '801 555 1234', ?)",
                     (iso(AHORA),))
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                     "VALUES ('u:user0', 'user0', 'confirmar_numero', ?, ?, ?)", (iso(AHORA),) * 3)
    detector.guardar(conn, leer(api_reel(1)), AHORA)  # ahora comenta, y Studio trae su ID estable

    k = conn.execute("SELECT * FROM contactos").fetchall()
    assert [f["user_id"] for f in k] == ["id0"]
    k = k[0]
    assert (k["contactado"], k["resultado"], k["etapa"], k["telefono"], k["humano"], k["humano_motivo"],
            k["respuestas_auto"], k["origen"]) == (1, "dm_enviado", "C", "8015551234", 1, "dio su número", 2, "mensaje")
    for tabla in ("leads", "mensajes", "envios", "comentarios"):
        assert {f[0] for f in conn.execute(f"SELECT user_id FROM {tabla}")} == {"id0"}, tabla


def test_reel_para_repasar(conn):
    detector.guardar(conn, leer(api_reel(1, reel="a")) + leer(api_reel(1, desde=1, reel="b"))
                     + leer(api_reel(1, desde=2, reel="viejo")), AHORA,
                     publicado={"viejo": AHORA - timedelta(days=90)})
    conn.execute("UPDATE reels SET ultimo_repaso = ? WHERE id = 'a'", (iso(AHORA - timedelta(hours=13)),))
    conn.commit()
    assert detector.reel_para_repasar(conn, AHORA) == "b"  # nunca repasado va primero; 'viejo' fuera de los 30 días
    conn.execute("UPDATE reels SET ultimo_repaso = ? WHERE id = 'b'", (iso(AHORA),))
    assert detector.reel_para_repasar(conn, AHORA) == "a"
    conn.execute("UPDATE reels SET ultimo_repaso = ? WHERE id = 'a'", (iso(AHORA - timedelta(hours=2)),))
    assert detector.reel_para_repasar(conn, AHORA) is None


# ------------------------------------------------------------------ con Chrome y el TikTok falso

hay_chrome = instalador_bot.hay_chrome()  # Mac, Windows (Program Files / LOCALAPPDATA) y Linux


@pytest.fixture
def falso():
    f = FalsoTikTok(publicado=T0 - 3600)
    f.reels["r1"] = [Comentario(f"c{i}", f"user{i}", f"info {i}", create_time=T0 + i) for i in range(20)]
    return f


@pytest.fixture
def det(conn, falso):
    nav = navegador.Navegador(visible=False).abrir()
    falso.instalar(nav.contexto)
    try:
        yield detector.DetectorTikTok(conn, PantallasTikTok(nav, conn, random.Random(1), carga_ms=5_000, elemento_ms=2_000))
    finally:
        nav.cerrar()


@pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")
def test_studio_y_repaso_con_chrome(det, falso, conn):
    assert det.leer(AHORA) == 20  # Studio (2 cargas de 10) + repaso de r1
    reel = conn.execute("SELECT publicado_en, ultimo_repaso FROM reels WHERE id = 'r1'").fetchone()
    assert reel["publicado_en"] == iso(datetime.fromtimestamp(falso.publicado, UTC)) and reel["ultimo_repaso"]

    falso.reels["r1"].append(Comentario("c20", "user20", "nuevo", create_time=T0 + 100))
    assert det.leer(AHORA + timedelta(minutes=10)) == 1  # releer: solo el nuevo; Studio para al llegar a uno conocido
    assert len([u for u in falso.visitas if "tiktokstudio/api" in u]) == 3  # 2 + 1: no volvió a bajar entera


@pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")
def test_repaso_recupera_lo_que_studio_no_enseno(det, falso, conn):
    det.leer(AHORA)
    conn.execute("DELETE FROM comentarios WHERE id = 'c3'")  # como si Studio no lo hubiera enseñado
    conn.commit()
    assert det.leer(AHORA + timedelta(minutes=10)) == 0  # aún no toca repasar
    assert det.leer(AHORA + timedelta(hours=13)) == 1
    assert conn.execute("SELECT estado FROM comentarios WHERE id = 'c3'").fetchone()[0] == "en_cola"
    assert falso.publicados == [] and falso.enviados == []  # leer nunca actúa
