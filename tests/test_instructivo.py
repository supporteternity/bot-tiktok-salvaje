"""Instructivo del cliente (§17): etapas, conversación, leads, seguimiento, bienvenida, recordatorios y orden."""

import json
import random
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bot import alertas, bandeja, config, ejecutor, leads, reglas, textos
from bot.bucle import Bucle
from bot.freno import Freno
from bot.pantallas import ChatEnLista, ChatLeido, ErrorPantalla, MensajeChat
from conftest import AHORA, meter_comentario
from test_estados import PantallasFalsas, RitmoLibre, estado, hasta_parar


@dataclass
class PantallasConBandeja(PantallasFalsas):
    chats: list = field(default_factory=list)  # ChatEnLista
    leidos: dict = field(default_factory=dict)  # conv_id -> ChatLeido
    seguidores: list = field(default_factory=list)
    fallo_bandeja: bool = False
    aceptar_pedido: list = field(default_factory=list)

    def leer_bandeja(self):
        if self.fallo_bandeja:
            raise ErrorPantalla("no carga la lista de mensajes")
        return list(self.chats)

    def leer_chat(self, chat, aceptar):
        self.aceptar_pedido.append(aceptar)
        return self.leidos[chat.conv_id]

    def seguidores_nuevos(self):
        return list(self.seguidores)


@pytest.fixture
def todo(conn, monkeypatch):
    """Base con los textos del instructivo, modo real, «todos» y correo simulado."""
    config.guardar(conn, "modo_prueba", False)
    config.guardar(conn, "modo_publica", "todos")
    textos.sembrar(conn)
    correos = []
    monkeypatch.setattr(alertas, "_enviar", lambda asunto, cuerpo, dest, adjunto=None: correos.append((asunto, cuerpo, dest)) or True)
    config.guardar(conn, "emails_alerta", ["equipo@ejemplo.com"])
    p, r = PantallasConBandeja(), RitmoLibre()
    ej = ejecutor.Ejecutor(conn, p, r, Freno(conn), random.Random(0))
    return conn, p, r, ej, correos


def contacto(conn, usuario):
    return conn.execute("SELECT * FROM contactos WHERE usuario = ?", (usuario,)).fetchone()


def tipos_publicos(p):
    return [t for (_, t, _) in p.respuestas]


# ---------------------------------------------------------------- comentarios (D35, D36)


def test_comentario_nuevo_dm_con_enlace_y_respuesta_publica_etapa_b(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana", reel="r9")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado"
    assert "academiasalvaje.com/vendedorsalvaje" in p.envios[0][1] and "utm_content=r9" in p.envios[0][1]
    assert tipos_publicos(p)[0] in textos.TEXTOS_INSTRUCTIVO["respuesta"]
    k = contacto(conn, "ana")
    assert (k["etapa"], k["reel_origen"], k["origen"]) == ("B", "r9", "comentario")


def test_ya_habiamos_hablado_no_manda_enlace_y_recuerda_en_publico(todo):
    """D36: con mensajes nuestros en el chat (p. ej. de ManyChat), no se manda el enlace otra vez."""
    conn, p, r, ej, _ = todo
    p.ya_hablados.add("ana")
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert p.envios == []
    assert estado(conn, "c1") == "completado_recordatorio"
    assert tipos_publicos(p) == [t for t in tipos_publicos(p) if t in textos.TEXTOS_INSTRUCTIVO["recordatorio"]]
    assert contacto(conn, "ana")["etapa"] == "B"


def test_vuelve_a_comentar_en_el_mismo_reel_recordatorio_una_vez_al_dia(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana", hace_min=60 * 30)
    hasta_parar(ej, AHORA - timedelta(hours=26))  # atendida hace más de un día
    meter_comentario(conn, "c2", "ana", hace_min=5)  # mismo reel (r1)
    meter_comentario(conn, "c3", "ana", hace_min=4)
    hasta_parar(ej)
    assert estado(conn, "c2") == "completado_recordatorio"
    assert estado(conn, "c3") == "saltado_contactado"  # el mismo día, solo una
    assert len(p.envios) == 1  # el enlace, una sola vez
    assert tipos_publicos(p)[-1] in textos.TEXTOS_INSTRUCTIVO["recordatorio"]


def test_comenta_en_otro_reel_recibe_otra_vez_el_mensaje_privado(todo):
    """D49 (pedido del usuario, 26 nov): ya recibió el enlace y comenta en un reel donde nunca había comentado →
    otra vez el mensaje privado (aunque el enlace ya esté en el chat). Un segundo reel el mismo día: solo uno."""
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana", hace_min=60 * 30)
    hasta_parar(ej, AHORA - timedelta(hours=26))  # atendida hace más de un día
    p.ya_hablados.add("ana")  # su chat ya tiene nuestro enlace
    meter_comentario(conn, "c2", "ana", reel="r2", hace_min=5)
    meter_comentario(conn, "c3", "ana", reel="r3", hace_min=4)
    hasta_parar(ej)
    assert len(p.envios) == 2 and "utm_content=r2" in p.envios[1][1]
    assert estado(conn, "c2") == "completado"
    assert estado(conn, "c3") == "saltado_contactado"  # dos enlaces seguidos parecen spam: el de r3 no sale


def test_otro_reel_pero_atendida_hace_poco_solo_un_enlace(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana", hace_min=60)
    hasta_parar(ej, AHORA - timedelta(hours=2))
    meter_comentario(conn, "c2", "ana", reel="r2", hace_min=5)
    hasta_parar(ej)
    assert len(p.envios) == 1
    assert estado(conn, "c2") in ("completado_recordatorio", "saltado_contactado")


def test_otro_reel_pero_ya_dio_su_numero_no_se_le_manda_el_enlace(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana", hace_min=60 * 30)
    hasta_parar(ej, AHORA - timedelta(hours=26))
    with conn:
        conn.execute("UPDATE contactos SET etapa = 'C', telefono = '8015551234' WHERE usuario = 'ana'")
    meter_comentario(conn, "c2", "ana", reel="r2", hace_min=5)
    hasta_parar(ej)
    assert len(p.envios) == 1
    assert tipos_publicos(p)[-1] in textos.TEXTOS_INSTRUCTIVO["ya_en_contacto"]


def test_vuelve_a_comentar_justo_despues_no_recibe_nada_mas(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana", hace_min=10)
    meter_comentario(conn, "c2", "ana", hace_min=9)
    hasta_parar(ej)
    assert estado(conn, "c2") == "saltado_contactado" and len(p.respuestas) == 1


@pytest.mark.parametrize("etapa, esperado", [("C", "ya_en_contacto"), ("D", "ya_en_contacto"), ("B", "recordatorio")])
def test_tipo_de_recordatorio_segun_etapa(todo, etapa, esperado):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana")
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado', etapa = ? WHERE usuario = 'ana'", (etapa,))
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado_recordatorio"
    assert tipos_publicos(p)[0] in textos.TEXTOS_INSTRUCTIVO[esperado]


def test_mensajes_cerrados_vuelve_a_comentar_otra_vez_mandame_msj(todo):
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado, ultimo_recordatorio_en) "
                     "VALUES ('u:ana', 'ana', 1, 'dm_cerrado', '2026-11-01T00:00:00+00:00')")
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert tipos_publicos(p)[0] in textos.TEXTOS_INSTRUCTIVO["respuesta_alt"]


def test_ofensivo_que_vuelve_a_comentar_no_recibe_respuesta_publica(todo):
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado, etapa) "
                     "VALUES ('u:ana', 'ana', 1, 'dm_enviado', 'B')")
    meter_comentario(conn, "c1", "ana", texto="eres un idiota")
    hasta_parar(ej)
    assert estado(conn, "c1") == "saltado_contactado" and p.respuestas == []


def test_numero_nunca_en_publico(todo):
    """D44: si un texto público llevara un teléfono, no se publica."""
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("UPDATE textos SET contenido = 'Llámanos al 801 555 1234' WHERE tipo = 'respuesta'")
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert p.respuestas == [] and estado(conn, "c1") == "error_final"
    assert textos.numero_en_publico("respuesta", "801-555-1234") and not textos.numero_en_publico("dm", "801-555-1234")


# ---------------------------------------------------------------- orden (D42)


def test_orden_videos_recientes_y_mas_me_gusta(todo):
    conn, p, r, ej, _ = todo
    config.guardar(conn, "orden_cola", "videos_recientes")
    meter_comentario(conn, "viejo1", "a", reel="viejo", hace_min=50)
    meter_comentario(conn, "nuevo_poco", "b", reel="nuevo", hace_min=40)
    meter_comentario(conn, "nuevo_mucho", "c", reel="nuevo", hace_min=10)
    with conn:
        conn.execute("UPDATE reels SET publicado_en = '2026-11-01T00:00:00+00:00' WHERE id = 'viejo'")
        conn.execute("UPDATE reels SET publicado_en = '2026-11-20T00:00:00+00:00' WHERE id = 'nuevo'")
        conn.execute("UPDATE comentarios SET likes = 30 WHERE id = 'nuevo_mucho'")
    orden = []
    while (cid := reglas.siguiente(conn, AHORA)) is not None:
        orden.append(cid)
        with conn:
            conn.execute("UPDATE comentarios SET estado = 'completado' WHERE id = ?", (cid,))
    assert orden == ["nuevo_mucho", "nuevo_poco", "viejo1"]
    config.guardar(conn, "orden_cola", "llegada")


# ---------------------------------------------------------------- bandeja (D37)


def chat(conv, previa="x", solicitud=False):
    return ChatEnLista(conv_id=conv, nombre="Ana", vista_previa=previa, solicitud=solicitud)


def leido(usuario, *mensajes):
    return ChatLeido(usuario=usuario, mensajes=tuple(MensajeChat(propio=m.startswith("BOT:"), texto=m.removeprefix("BOT:"))
                                                     for m in mensajes))


def test_primera_lectura_de_bandeja_no_contesta_lo_de_antes(todo):
    conn, p, r, ej, _ = todo
    p.chats = [chat("1", "hola")]
    p.leidos["1"] = leido("ana", "hola, mi número es 8015551234")
    lector = bandeja.LectorBandeja(conn, p)
    assert lector.leer(AHORA) == 0
    assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
    assert lector.leer(AHORA) == 0  # nada cambió: no se abre ningún chat


def preparar_lector(conn, p):
    lector = bandeja.LectorBandeja(conn, p)
    lector.leer(AHORA)  # primera lectura (bandeja vacía)
    return lector


def test_da_su_numero_lead_correo_confirmacion_y_pasa_a_persona(todo):
    conn, p, r, ej, correos = todo
    meter_comentario(conn, "c1", "ana", reel="r9")
    with conn:
        conn.execute("UPDATE reels SET palabra_clave = 'VENTAS' WHERE id = 'r9'")
    hasta_parar(ej)
    lector = preparar_lector(conn, p)
    p.chats = [chat("1", "ana: 801 555 1234")]
    p.leidos["1"] = leido("ana", "BOT:Que rollo salvaje…", "va, mi cel es 801 555 1234")
    assert lector.leer(AHORA) == 1
    lead = conn.execute("SELECT * FROM leads").fetchone()
    assert (lead["usuario"], lead["telefono"], lead["reel_id"], lead["palabra_clave"]) == ("ana", "8015551234", "r9", "VENTAS")
    assert lead["correo_en"] and "801 555 1234" in correos[-1][0] and "VENTAS" in correos[-1][1]
    k = contacto(conn, "ana")
    assert (k["etapa"], k["humano"]) == ("C", 1)
    hasta_parar(ej)
    assert p.envios[-1][1] == textos.TEXTOS_INSTRUCTIVO["confirmar_numero"][0]
    # vuelve a escribir: ya la atiende el equipo, el bot no contesta
    p.chats = [chat("1", "ana: gracias")]
    p.leidos["1"] = leido("ana", "BOT:Que rollo salvaje…", "va, mi cel es 801 555 1234", "BOT:Ya tenemos tu número…", "gracias!!")
    lector.leer(AHORA)
    n = len(p.envios)
    hasta_parar(ej)
    assert len(p.envios) == n and conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1


def test_pregunta_precio_pasa_a_persona_y_avisa(todo):
    conn, p, r, ej, correos = todo
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    lector = preparar_lector(conn, p)
    p.chats = [chat("1", "cuanto cuesta")]
    p.leidos["1"] = leido("ana", "BOT:info", "¿Cuánto cuesta?")
    lector.leer(AHORA)
    assert contacto(conn, "ana")["humano"] == 1
    assert "@ana" in correos[-1][0]
    assert conn.execute("SELECT COUNT(*) FROM envios").fetchone()[0] == 0


def test_duda_pide_numero_e_interes_anima(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    lector = preparar_lector(conn, p)
    p.chats = [chat("1", "a")]
    p.leidos["1"] = leido("ana", "BOT:info", "me interesa bro")
    lector.leer(AHORA)
    hasta_parar(ej)
    assert p.envios[-1][1] == textos.TEXTOS_INSTRUCTIVO["animo"][0]
    p.chats = [chat("1", "b")]
    p.leidos["1"] = leido("ana", "BOT:info", "me interesa bro", "BOT:ánimo", "¿y cómo es?")
    lector.leer(AHORA)
    hasta_parar(ej)
    assert p.envios[-1][1] == textos.TEXTOS_INSTRUCTIVO["pedir_numero"][0]
    assert contacto(conn, "ana")["respuestas_auto"] == 2


def test_escribe_sin_haber_recibido_info_recibe_el_enlace_y_acepta_solicitud(todo):
    """Tras «Tírame msj» la persona escribe (llega como solicitud): se acepta y se le manda el enlace."""
    conn, p, r, ej, _ = todo
    lector = preparar_lector(conn, p)
    p.chats = [chat("9", "dame info", solicitud=True)]
    p.leidos["9"] = leido("pepe", "dame la info")
    lector.leer(AHORA)
    assert p.aceptar_pedido == [True]
    hasta_parar(ej)
    assert "academiasalvaje.com" in p.envios[-1][1] and "utm_content=chat" in p.envios[-1][1]
    k = contacto(conn, "pepe")
    assert (k["etapa"], k["contactado"], k["origen"]) == ("B", 1, "mensaje")


def test_en_modo_de_prueba_no_acepta_solicitudes(todo):
    conn, p, r, ej, _ = todo
    config.guardar(conn, "modo_prueba", True)
    lector = preparar_lector(conn, p)
    p.chats = [chat("9", "hola", solicitud=True)]
    p.leidos["9"] = leido("pepe", "hola")
    lector.leer(AHORA)
    assert p.aceptar_pedido == [False]


def test_nuevos_de_la_persona():
    m = lambda propio, t: MensajeChat(propio, t)  # noqa: E731
    msgs = (m(True, "info"), m(False, "hola"), m(True, "ánimo"), m(False, "ok"), m(False, "801"))
    assert bandeja.nuevos_de_la_persona(msgs, bandeja.firma(msgs[1])) == ["ok", "801"]
    assert bandeja.nuevos_de_la_persona(msgs, None) == ["ok", "801"]
    assert bandeja.nuevos_de_la_persona(msgs, bandeja.firma(msgs[-1])) == []
    assert bandeja.nuevos_de_la_persona((m(False, "a"),) * 8, None) == ["a"] * 5


# ---------------------------------------------------------------- leads (D38) y seguimiento (D39)


def test_lead_a_gohighlevel_por_webhook(todo):
    conn, p, r, ej, _ = todo
    recibido = []

    class Manejador(BaseHTTPRequestHandler):
        def do_POST(self):
            recibido.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Manejador)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    config.guardar(conn, "webhook_leads", f"http://127.0.0.1:{srv.server_port}/hook")
    meter_comentario(conn, "c1", "ana", reel="r9")
    leads.registrar(conn, "u:ana", "8015551234", AHORA)
    srv.server_close()
    assert recibido[0]["telefono"] == "8015551234" and recibido[0]["usuario_tiktok"] == "@ana"
    assert recibido[0]["video"].endswith("/video/r9")
    assert conn.execute("SELECT webhook_en FROM leads").fetchone()[0]


def test_lead_sin_conexion_se_reintenta(todo):
    conn, p, r, ej, _ = todo
    config.guardar(conn, "webhook_leads", "http://127.0.0.1:9/no-hay-nadie")
    meter_comentario(conn, "c1", "ana")
    leads.registrar(conn, "u:ana", "8015551234", AHORA)
    fila = conn.execute("SELECT webhook_en, webhook_error FROM leads").fetchone()
    assert fila[0] is None and "no se pudo conectar" in fila[1]
    llamadas = []
    original = leads._webhook
    leads._webhook = lambda *a: llamadas.append(1) or None
    try:
        leads.reintentar(conn, AHORA + timedelta(minutes=5))
        assert llamadas == []  # aún no pasaron 10 min
        leads.reintentar(conn, AHORA + timedelta(minutes=11))
        assert llamadas == [1] and conn.execute("SELECT webhook_en FROM leads").fetchone()[0]
    finally:
        leads._webhook = original


def test_webhook_que_redirige_no_da_el_lead_por_entregado(todo):
    """Auditoría 26 nov: ante una redirección (p. ej. http → https), urllib repetía la petición como GET y SIN el
    lead; el destino contestaba 200 y el lead quedaba como entregado sin haber llegado a GoHighLevel."""
    conn, p, r, ej, _ = todo
    peticiones = []

    class Manejador(BaseHTTPRequestHandler):
        def do_POST(self):
            peticiones.append(("POST", self.path))
            self.send_response(302)
            self.send_header("Location", "/destino")
            self.end_headers()

        def do_GET(self):
            peticiones.append(("GET", self.path))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Manejador)
    srv.timeout = 3
    hilo = threading.Thread(target=lambda: [srv.handle_request() for _ in range(2)], daemon=True)
    hilo.start()
    config.guardar(conn, "webhook_leads", f"http://127.0.0.1:{srv.server_port}/hook")
    meter_comentario(conn, "c1", "ana")
    leads.registrar(conn, "u:ana", "8015551234", AHORA)
    hilo.join(5)
    srv.server_close()
    assert peticiones == [("POST", "/hook")]  # no se sigue la redirección
    fila = conn.execute("SELECT webhook_en, webhook_error FROM leads").fetchone()
    assert fila[0] is None and "redirige" in fila[1]


def test_correo_de_leads_que_falla_espera_10_min_antes_de_reintentar(todo, monkeypatch):
    """Auditoría 26 nov: si el correo fallaba (p. ej. Google anuló la contraseña de aplicación), el correo de cada
    lead se reintentaba en CADA vuelta del bot: Gmail podía bloquear la cuenta y el bot se frenaba."""
    import smtplib

    conn, p, r, ej, _ = todo
    intentos = []

    def falla(*_a, **_k):
        intentos.append(1)
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(alertas, "correo_configurado", lambda: True)
    monkeypatch.setattr(alertas, "_enviar", falla)
    meter_comentario(conn, "c1", "ana")
    meter_comentario(conn, "c2", "luis")
    leads.registrar(conn, "u:ana", "8015551234", AHORA)
    assert len(intentos) == 1
    leads.registrar(conn, "u:luis", "8015554321", AHORA + timedelta(seconds=30))  # otro lead: el correo sigue roto
    for s in range(1, 300, 5):  # 5 min de vueltas del bot
        leads.reintentar(conn, AHORA + timedelta(seconds=s))
    assert len(intentos) == 1
    monkeypatch.setattr(alertas, "_enviar", lambda *a, **k: intentos.append(1) or True)  # se arregla el correo
    leads.reintentar(conn, AHORA + timedelta(minutes=11))
    assert len(intentos) == 3  # pasada la espera, salen los dos
    assert conn.execute("SELECT COUNT(*) FROM leads WHERE correo_en IS NOT NULL").fetchone()[0] == 2


def test_ya_hable_con_la_persona_seguimiento_con_vendedor(todo):
    conn, p, r, ej, _ = todo
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    lead_id = leads.registrar(conn, contacto(conn, "ana")["user_id"], "8015551234", AHORA)
    leads.marcar_hablado(conn, lead_id, "Carlos", AHORA)
    leads.marcar_hablado(conn, lead_id, "Otro", AHORA)  # dos clics: un solo seguimiento
    assert contacto(conn, "ana")["etapa"] == "D"
    n = len(p.envios)
    hasta_parar(ej)
    assert len(p.envios) == n  # todavía no pasó la espera (1 h)
    hasta_parar(ej, AHORA + timedelta(hours=1, minutes=1))
    assert p.envios[-1][1] in ("Que rollo bro, ¿cómo te fue?", "Que rollo bro, ¿cómo te fue con Carlos?")
    assert conn.execute("SELECT COUNT(*) FROM envios WHERE tipo = 'seguimiento'").fetchone()[0] == 1


def test_seguimiento_sin_vendedor_quita_el_con():
    assert textos.rellenar("Que rollo bro, ¿cómo te fue con {vendedor}?", None, None) == "Que rollo bro, ¿cómo te fue?"
    assert textos.rellenar("¿Cómo te fue con {vendedor}?", None, None, vendedor="Ana") == "¿Cómo te fue con Ana?"


# ---------------------------------------------------------------- envíos: errores y textos que faltan


def test_envio_sin_texto_se_cancela_sin_frenar(todo):
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("DELETE FROM textos WHERE tipo = 'animo'")
        conn.execute("INSERT INTO contactos (user_id, usuario, etapa) VALUES ('u:ana', 'ana', 'B')")
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) VALUES "
                     "('u:ana', 'ana', 'animo', '2026-11-24T17:00:00+00:00', '2026-11-24T17:00:00+00:00', '2026-11-24T17:00:00+00:00')")
    paso = ej.paso(AHORA)
    assert paso.problema is None
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "cancelado"


def test_envio_que_falla_antes_de_enviar_se_reintenta_y_tras_corte_queda_dudoso(todo):
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, etapa) VALUES ('u:ana', 'ana', 'B')")
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) VALUES "
                     "('u:ana', 'ana', 'pedir_numero', '2026-11-24T17:00:00+00:00', '2026-11-24T17:00:00+00:00', '2026-11-24T17:00:00+00:00')")
    err = ErrorPantalla("no carga")
    err.sin_enviar = True
    p.fallo_en, p.fallo = "envio", err
    ej.paso(AHORA)
    fila = conn.execute("SELECT estado, intentos FROM envios").fetchone()
    assert (fila[0], fila[1]) == ("pendiente", 1)
    with conn:
        conn.execute("UPDATE envios SET estado = 'enviando'")
    p.fallo_en = None
    ej.paso(AHORA + timedelta(hours=1))
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "dudoso"  # nunca se reenvía a ciegas


def test_enlace_de_la_bandeja_reintentado_no_sale_si_ya_lo_recibio_por_su_comentario(todo):
    """Auditoría 26 nov: Ana comenta y además escribe «info» por privado. El enlace de la bandeja falla antes de
    enviar y se reprograma; mientras, su comentario le manda el enlace. Al reintentar no puede salir otra vez."""
    conn, p, r, ej, _ = todo
    lector = preparar_lector(conn, p)
    meter_comentario(conn, "c1", "ana", user_id="u:ana")
    p.chats = [chat("7", "info")]
    p.leidos["7"] = leido("ana", "info")
    lector.leer(AHORA)
    assert conn.execute("SELECT tipo FROM envios").fetchone()[0] == "dm"

    err = ErrorPantalla("no se abrió el chat")
    err.sin_enviar = True
    p.fallo_en, p.fallo = "envio", err
    ej.paso(AHORA)  # el envío de la bandeja falla antes de enviar → se reintenta en 10 min
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "pendiente"
    p.fallo_en = None
    hasta_parar(ej)  # mientras, el comentario le manda el enlace
    hasta_parar(ej, AHORA + timedelta(minutes=11))  # llega la hora del reintento
    con_enlace = [e for e in p.envios if "academiasalvaje.com" in e[1]]
    assert len(con_enlace) == 1, p.envios
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "cancelado"


def test_enlace_de_la_bandeja_no_sale_si_ya_hay_mensajes_nuestros_en_el_chat(todo):
    """D36 también para el enlace que pide la bandeja: si en el chat ya hay mensajes nuestros (ManyChat, el
    equipo, o un envío anterior que no se pudo comprobar), no se manda otra vez."""
    conn, p, r, ej, _ = todo
    lector = preparar_lector(conn, p)
    p.chats = [chat("8", "info")]
    p.leidos["8"] = leido("pepe", "info")
    lector.leer(AHORA)
    p.ya_hablados.add("pepe")
    hasta_parar(ej)
    assert [e for e in p.envios if "academiasalvaje.com" in e[1]] == []


def test_prueba_a_real_repone_el_enlace_simulado_de_la_bandeja(todo):
    """Auditoría 26 nov: en modo de prueba, a Ana (escribió «info») se le «mandó» el enlace solo en simulado y quedó
    como atendida (etapa B). Al pasar a real tiene que recibirlo de verdad, una vez, y no un recordatorio público
    de algo que nunca le llegó."""
    conn, p, r, ej, _ = todo
    config.guardar(conn, "modo_prueba", True)
    lector = preparar_lector(conn, p)
    p.chats = [chat("5", "info")]
    p.leidos["5"] = leido("ana", "info")
    lector.leer(AHORA)
    hasta_parar(ej)
    assert [e[2] for e in p.envios] == [True]  # simulado: no le llegó nada
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "simulado"

    config.guardar(conn, "modo_prueba", False)
    reglas.reponer_simulados(conn, AHORA)
    k = contacto(conn, "ana")
    assert (k["contactado"], k["etapa"]) == (0, "A")
    hasta_parar(ej, AHORA + timedelta(minutes=1))
    reales = [e for e in p.envios if e[2] is False]
    assert len(reales) == 1 and "academiasalvaje.com" in reales[0][1]
    assert contacto(conn, "ana")["etapa"] == "B"
    reglas.reponer_simulados(conn, AHORA + timedelta(minutes=2))  # pasar otra vez a real no repone nada
    hasta_parar(ej, AHORA + timedelta(minutes=3))
    assert len([e for e in p.envios if e[2] is False]) == 1


def test_prueba_a_real_no_toca_a_quien_ya_recibio_algo_de_verdad(todo):
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado, etapa) "
                     "VALUES ('u:ana', 'ana', 1, 'dm_enviado', 'B')")
        for estado_envio in ("enviado", "simulado"):  # un enlace real y, después, una respuesta simulada
            conn.execute("INSERT INTO envios (user_id, usuario, tipo, estado, creado_en, listo_desde, actualizado_en) "
                         "VALUES ('u:ana', 'ana', ?, ?, ?, ?, ?)",
                         ("dm" if estado_envio == "enviado" else "animo", estado_envio, *(AHORA.isoformat(),) * 3))
    reglas.reponer_simulados(conn, AHORA)
    k = contacto(conn, "ana")
    assert (k["contactado"], k["etapa"]) == (1, "B")
    assert [f[0] for f in conn.execute("SELECT estado FROM envios ORDER BY id")] == ["enviado", "simulado_repuesto"]


# ---------------------------------------------------------------- seguidores nuevos (D40)


def test_bienvenida_solo_a_seguidores_nuevos(todo):
    conn, p, r, ej, _ = todo
    config.guardar(conn, "enlace_youtube", "https://youtu.be/abc")
    lector = bandeja.LectorBandeja(conn, p)
    p.seguidores = ["vieja"]
    assert lector.leer_seguidores(AHORA) == 0  # al encender: los de antes, sin bienvenida
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)  # ana ya recibió la info por su comentario
    p.seguidores = ["nueva", "ana", "vieja"]
    assert lector.leer_seguidores(AHORA) == 1
    hasta_parar(ej)
    assert p.envios[-1][0] == "nueva" and "https://youtu.be/abc" in p.envios[-1][1]
    k = contacto(conn, "nueva")
    assert (k["etapa"], k["contactado"], k["origen"]) == ("B", 1, "seguidor")
    accion = conn.execute("SELECT seguidor FROM acciones WHERE tipo = 'bienvenida'").fetchone()
    assert accion[0] == 1  # gasta el cupo de quien sigue la cuenta


def test_sin_enlace_de_youtube_no_hay_bienvenida(todo):
    conn, p, r, ej, _ = todo
    lector = bandeja.LectorBandeja(conn, p)
    p.seguidores = []
    lector.leer_seguidores(AHORA)
    p.seguidores = ["nueva"]
    assert lector.leer_seguidores(AHORA) == 0


# ---------------------------------------------------------------- bucle


class DetectorQuieto:
    def leer(self, ahora):
        return 0


def test_bandeja_que_falla_no_frena_al_bot(todo):
    conn, p, r, ej, correos = todo
    p.fallo_bandeja = True
    bucle = Bucle(conn, p, DetectorQuieto(), random.Random(0))
    bucle.vuelta(AHORA)
    estado_bot = conn.execute("SELECT modo FROM estado_bot").fetchone()[0]
    assert estado_bot == "activo"
    assert any("bandeja" in a for a, _, _ in correos)
    assert conn.execute("SELECT resultado FROM acciones WHERE tipo = 'bandeja'").fetchone()[0] == "error"


def test_chat_con_cabecera_vacia_se_reconoce_por_el_id_y_no_se_pierde(todo):
    """26 nov, TikTok real: la cabecera del chat sale «@» vacía. Se reconoce a la persona por el ID interno del chat;
    si no se puede, NO se marca como visto (sus mensajes se leen en la próxima vuelta)."""
    conn, p, r, ej, _ = todo
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado, etapa) "
                     "VALUES ('7000000000000000002', 'persona.uno', 1, 'dm_enviado', 'B')")
    lector = preparar_lector(conn, p)
    conv = "0:1:7000000000000000002:7000000000000000001"
    p.chats = [chat(conv, "me interesa")]
    p.leidos[conv] = leido(None, "BOT:info", "me interesa")
    lector.leer(AHORA)
    assert conn.execute("SELECT tipo FROM envios").fetchone()[0] == "animo"
    # desconocido sin @: no se contesta ni se marca como visto
    p.chats = [chat(conv, "me interesa"), chat("0:1:111:222", "hola")]
    p.leidos["0:1:111:222"] = leido(None, "hola")
    lector.leer(AHORA)
    assert conn.execute("SELECT 1 FROM conversaciones WHERE conv_id = '0:1:111:222'").fetchone() is None
    p.leidos["0:1:111:222"] = leido("pepe", "hola")
    lector.leer(AHORA)  # la próxima vez sí sale el @: se atiende
    assert conn.execute("SELECT COUNT(*) FROM envios WHERE usuario = 'pepe'").fetchone()[0] == 1
