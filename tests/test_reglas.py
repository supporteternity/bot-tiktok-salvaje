from bot import config, reglas
from conftest import AHORA, meter_comentario


def fila(conn, cid):
    return conn.execute("SELECT * FROM comentarios WHERE id = ?", (cid,)).fetchone()


# ---------------------------------------------------------------- piezas sueltas


def test_es_ofensivo_sin_mayusculas_ni_tildes_y_palabra_entera():
    palabras = ["imbécil", "pendejo"]
    assert reglas.es_ofensivo("Eres un IMBECIL", palabras)
    assert reglas.es_ofensivo("pendejo!!", palabras)
    assert not reglas.es_ofensivo("despendejoso no es palabra entera", palabras)
    assert not reglas.es_ofensivo("info por favor", palabras)
    assert not reglas.es_ofensivo(None, palabras)


def test_es_propio_cuenta_y_equipo_sin_arroba_ni_mayusculas():
    assert reglas.es_propio("@Academia", "academia", [])
    assert reglas.es_propio("setter1", "academia", ["@Setter1"])
    assert not reglas.es_propio("juan", "academia", ["setter1"])


# ---------------------------------------------------------------- evaluar sobre la base


def test_comentario_de_la_propia_cuenta_o_del_equipo_se_salta(conn):
    config.guardar(conn, "cuentas_equipo", ["setter1"])
    meter_comentario(conn, "c1", "academia")
    meter_comentario(conn, "c2", "setter1")
    assert reglas.evaluar(conn, "c1", AHORA) == "saltado_propio"
    assert reglas.evaluar(conn, "c2", AHORA) == "saltado_propio"
    assert fila(conn, "c1")["estado"] == "saltado_propio"


def test_persona_ya_contactada_se_salta(conn):
    meter_comentario(conn, "c1", "juan")
    conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado' WHERE user_id = 'u:juan'")
    assert reglas.evaluar(conn, "c1", AHORA) == "saltado_contactado"


def test_caducidad_apagada_por_defecto_y_encendida(conn):
    meter_comentario(conn, "viejo", "ana", hace_min=60 * 24 * 5)
    assert reglas.evaluar(conn, "viejo", AHORA) == reglas.ATENDER
    config.guardar(conn, "caducidad_dias", 3)
    assert reglas.evaluar(conn, "viejo", AHORA) == "caducado"


def test_ofensivo_se_marca_pero_se_atiende(conn):
    meter_comentario(conn, "c1", "troll", texto="estafa de pendejos, pendejo")
    assert reglas.evaluar(conn, "c1", AHORA) == reglas.ATENDER
    assert fila(conn, "c1")["ofensivo"] == 1
    assert fila(conn, "c1")["estado"] == "en_cola"


def test_ofensivo_en_solo_publicas_se_salta(conn):
    config.guardar(conn, "modo_publica", "solo_publicas")
    meter_comentario(conn, "c1", "troll", texto="pendejo")
    assert reglas.evaluar(conn, "c1", AHORA) == "saltado_ofensivo"


def test_si_la_persona_tiene_otro_comentario_a_medias_se_espera(conn):
    meter_comentario(conn, "c1", "luis", hace_min=30, estado="error")
    meter_comentario(conn, "c2", "luis", hace_min=10)
    assert reglas.evaluar(conn, "c2", AHORA) == reglas.ESPERAR
    assert fila(conn, "c2")["estado"] == "en_cola"


def test_comento_3_veces_en_2_reels_recibe_1_atencion(conn):
    meter_comentario(conn, "c1", "maria", reel="r1", hace_min=30)
    meter_comentario(conn, "c2", "maria", reel="r1", hace_min=20)
    meter_comentario(conn, "c3", "maria", reel="r2", hace_min=10)

    atendidos = []
    while (cid := reglas.siguiente(conn, AHORA)) is not None:
        atendidos.append(cid)
        # simula que el ejecutor lo atiende: persona contactada y comentario terminado
        reglas.marcar_contactado(conn, "u:maria", "dm_enviado", AHORA)
        conn.execute("UPDATE comentarios SET estado = 'completado' WHERE id = ?", (cid,))

    assert atendidos == ["c1"]  # el más antiguo
    assert fila(conn, "c2")["estado"] == "saltado_contactado"
    assert fila(conn, "c3")["estado"] == "saltado_contactado"


def test_siguiente_respeta_orden_de_llegada_y_salta_a_quien_espera(conn):
    meter_comentario(conn, "a_medias", "luis", hace_min=50, estado="dm_enviando")
    meter_comentario(conn, "luis2", "luis", hace_min=40)
    meter_comentario(conn, "ana1", "ana", hace_min=30)
    meter_comentario(conn, "pepe1", "pepe", hace_min=20)
    assert reglas.siguiente(conn, AHORA) == "ana1"


# ---------------------------------------------------------------- paso de modo de prueba a real


def _accion(conn, cid, resultado, tipo="dm"):
    with conn:
        conn.execute("INSERT INTO acciones (tipo, comentario_id, reel_id, resultado, hecho_en) VALUES (?, ?, 'r1', ?, ?)",
                     (tipo, cid, resultado, AHORA.isoformat()))


def test_quitar_modo_de_prueba_devuelve_a_la_cola_lo_simulado(conn):
    # ana: atendida solo en prueba → vuelve. Su segundo comentario (saltado) se queda como está.
    meter_comentario(conn, "c1", "ana", estado="completado")
    meter_comentario(conn, "c1b", "ana", estado="saltado_contactado")
    conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado' WHERE user_id = 'u:ana'")
    _accion(conn, "c1", "simulado")
    # beto: mensajes cerrados en prueba (dm cerrado + respuesta alternativa simulada) → vuelve
    meter_comentario(conn, "c2", "beto", estado="completado_cerrado")
    conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_cerrado' WHERE user_id = 'u:beto'")
    _accion(conn, "c2", "cerrado")
    _accion(conn, "c2", "simulado", tipo="respuesta_alt")
    # carla: ya recibió un mensaje de verdad → no se toca
    meter_comentario(conn, "c3", "carla", estado="completado")
    conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado' WHERE user_id = 'u:carla'")
    _accion(conn, "c3", "ok")

    assert reglas.reponer_simulados(conn, AHORA) == 2
    assert fila(conn, "c1")["estado"] == "en_cola" and fila(conn, "c2")["estado"] == "en_cola"
    assert fila(conn, "c1b")["estado"] == "saltado_contactado"
    assert fila(conn, "c3")["estado"] == "completado"
    contactado = dict(conn.execute("SELECT user_id, contactado FROM contactos"))
    assert contactado == {"u:ana": 0, "u:beto": 0, "u:carla": 1}
    assert reglas.evaluar(conn, "c1", AHORA) == reglas.ATENDER  # ahora sí se le escribe
    assert reglas.reponer_simulados(conn, AHORA) == 0  # una segunda vez no repone nada
