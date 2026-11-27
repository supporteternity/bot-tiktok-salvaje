"""Máquina de estados (§6) con pantallas simuladas: cada flecha del diagrama tiene su test."""

import random
from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from bot import config, ejecutor
from bot.freno import Freno
from bot.pantallas import AvisoTikTok, ErrorPantalla, InfoPerfil
from bot.ritmo import Decision, iso
from conftest import AHORA, meter_comentario


@dataclass
class PantallasFalsas:
    perfiles: dict = field(default_factory=dict)  # usuario -> InfoPerfil
    envio: str = "enviado"  # resultado de enviar_dm
    ya_enviado: bool | None = None
    respuesta: str = "ok"
    fallo_en: str | None = None  # "perfil" | "envio" | "respuesta"
    fallo: Exception | None = None
    envios: list = field(default_factory=list)
    respuestas: list = field(default_factory=list)
    ya_hablados: set = field(default_factory=set)  # D36: chats donde ya hay mensajes nuestros

    def _quiza_fallar(self, donde):
        if self.fallo_en == donde:
            raise self.fallo

    def abrir_perfil(self, usuario):
        self._quiza_fallar("perfil")
        return self.perfiles.get(usuario, InfoPerfil(existe=True, puede_mensaje=True, nombre="Ana López"))

    def enviar_dm(self, usuario, texto, simulado, solo_si_nuevo=False):
        self._quiza_fallar("envio")
        if solo_si_nuevo and usuario in self.ya_hablados:
            return "ya_hablado"
        self.envios.append((usuario, texto, simulado))
        return self.envio

    def dm_ya_enviado(self, usuario):
        return self.ya_enviado

    def responder(self, reel_id, comentario_id, texto, simulado):
        self._quiza_fallar("respuesta")
        self.respuestas.append((comentario_id, texto, simulado))
        return self.respuesta


class RitmoLibre:
    """Siempre deja actuar; apunta lo que se hizo."""

    def __init__(self):
        self.hechos = []
        self.cupos = {False: True, True: True}  # ¿queda cupo diario de mensajes? (no seguidores, seguidores)

    def puede(self, tipo, ahora):
        return Decision(True)

    def cupo_dm(self, ahora, seguidor):
        return Decision(True) if self.cupos[bool(seguidor)] else Decision(False, ahora + timedelta(hours=5), "tope_dm_dia")

    def hecho(self, tipo, ahora):
        self.hechos.append(tipo)


@pytest.fixture
def entorno(conn):
    config.guardar(conn, "modo_prueba", False)
    config.guardar(conn, "landing", "https://academia.com")
    with conn:
        for tipo, txt in [("dm", "Hola {nombre}, mira {landing}"), ("respuesta", "¡Te escribí!"),
                          ("respuesta_alt", "Síguenos y escríbenos ACADEMIA")]:
            conn.execute("INSERT INTO textos (tipo, contenido) VALUES (?, ?)", (tipo, txt))
    p, r = PantallasFalsas(), RitmoLibre()
    ej = ejecutor.Ejecutor(conn, p, r, Freno(conn), random.Random(0))
    return conn, p, r, ej


def estado(conn, cid):
    return conn.execute("SELECT estado FROM comentarios WHERE id = ?", (cid,)).fetchone()[0]


def contacto(conn, usuario):
    return conn.execute("SELECT * FROM contactos WHERE usuario = ?", (usuario,)).fetchone()


def acciones(conn):
    return [(a["tipo"], a["resultado"]) for a in conn.execute("SELECT * FROM acciones ORDER BY id")]


def hasta_parar(ej, ahora=AHORA, max_pasos=20):
    for _ in range(max_pasos):
        if not ej.paso(ahora).trabajo:
            return


# ---------------------------------------------------------------- solo_cerrados (por defecto)


def test_abierto_solo_cerrados_dm_enviado_y_completado_sin_publica(entorno):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado"
    assert p.envios[0][1].startswith("Hola Ana, mira https://academia.com?")
    assert p.respuestas == []
    assert contacto(conn, "ana")["contactado"] == 1 and contacto(conn, "ana")["resultado"] == "dm_enviado"
    assert acciones(conn) == [("dm", "ok")] and r.hechos == ["dm"]


def test_cerrado_sin_boton_va_a_respuesta_alternativa(entorno):
    conn, p, r, ej = entorno
    p.perfiles["bea"] = InfoPerfil(existe=True, puede_mensaje=False)
    meter_comentario(conn, "c1", "bea")
    ej.paso(AHORA)
    assert estado(conn, "c1") == "alt_pendiente"
    assert contacto(conn, "bea")["resultado"] == "dm_cerrado"
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado_cerrado"
    assert p.respuestas == [("c1", "Síguenos y escríbenos ACADEMIA", False)]
    assert acciones(conn) == [("dm", "cerrado"), ("respuesta_alt", "ok")]
    # El intento cerrado no cuenta como acción (en `acciones` va como «cerrado», fuera de los topes), pero abrir el
    # perfil sí lleva su pausa («perfil»): si no, el bot abriría perfiles uno tras otro (auditoría 26 nov).
    assert r.hechos == ["perfil", "respuesta_alt"]


@pytest.mark.parametrize("caso", ["cerrado", "no_existe", "ya_atendida"])
def test_abrir_un_perfil_siempre_lleva_pausa(entorno, caso):
    """Auditoría 26 nov: con mensajes cerrados, cuenta inexistente o persona ya atendida (se ve al unir por ID) el
    bot abría el perfil y pasaba al siguiente sin pausa. Ahora cada perfil abierto espera como una acción más."""
    conn, p, r, ej = entorno
    if caso == "cerrado":
        p.perfiles["bea"] = InfoPerfil(existe=True, puede_mensaje=False)
    elif caso == "no_existe":
        p.perfiles["bea"] = InfoPerfil(existe=False, puede_mensaje=False)
    else:
        with conn:
            conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado) VALUES ('77', 'bea_antes', 1, 'dm_enviado')")
        meter_comentario(conn, "c0", "bea_antes", estado="completado", user_id="77")  # ya comentó en este reel
        p.perfiles["bea"] = InfoPerfil(existe=True, puede_mensaje=True, user_id="77")
    meter_comentario(conn, "c1", "bea")
    ej.paso(AHORA)
    assert r.hechos == ["perfil"] and p.envios == []


def test_perfiles_cerrados_seguidos_no_se_abren_sin_pausa(conn):
    """Con el ritmo real (nivel 3) y el tope de respuestas públicas de la hora gastado: antes abría 15 perfiles
    con mensajes cerrados en 15 segundos."""
    from bot.ritmo import Ritmo, iso

    config.guardar(conn, "modo_prueba", False)
    config.guardar(conn, "landing", "https://academia.com")
    with conn:
        for tipo, txt in [("dm", "Hola {nombre}, mira {landing}"), ("respuesta_alt", "Escríbenos")]:
            conn.execute("INSERT INTO textos (tipo, contenido) VALUES (?, ?)", (tipo, txt))
        for i in range(18):  # 75 % de 25/h en respuestas públicas, hace 40–58 min
            conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en) VALUES ('respuesta_alt', 'ok', ?)",
                         (iso(AHORA - timedelta(minutes=58 - i)),))
    p = PantallasFalsas()
    abiertos = []

    def abrir(usuario):
        abiertos.append(usuario)
        return InfoPerfil(existe=True, puede_mensaje=False)

    p.abrir_perfil = abrir
    rng = random.Random(0)
    ej = ejecutor.Ejecutor(conn, p, Ritmo(conn, rng), Freno(conn), rng)
    for i in range(15):
        meter_comentario(conn, f"c{i}", f"persona{i}", hace_min=30 - i)
    for s in range(15):  # el bucle vuelve 1 s después de cada paso con trabajo
        ej.paso(AHORA + timedelta(seconds=s))
    assert len(abiertos) == 1


def test_cerrado_al_enviar_tambien_es_cerrado(entorno):
    conn, p, r, ej = entorno
    p.envio = "cerrado"
    meter_comentario(conn, "c1", "bea")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado_cerrado"


def test_cerrado_y_ofensivo_sin_publica(entorno):
    conn, p, r, ej = entorno
    p.perfiles["troll"] = InfoPerfil(existe=True, puede_mensaje=False)
    meter_comentario(conn, "c1", "troll", texto="pendejo")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado_cerrado"
    assert p.respuestas == []


@pytest.mark.parametrize("ofensivo", [False, True])
def test_tiktok_no_deja_enviar_se_le_contesta_en_publico_y_al_escribir_recibe_el_enlace(entorno, ofensivo):
    """D47 (visto con TikTok real el 26 nov): TikTok rechaza en silencio el primer mensaje a algunos desconocidos.
    No se reenvía nunca; se le contesta en público («escríbenos») y, cuando escribe, recibe el enlace."""
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    p.envio = "dudoso"
    meter_comentario(conn, "c1", "ana")
    with conn:
        conn.execute("UPDATE comentarios SET ofensivo = ? WHERE id = 'c1'", (int(ofensivo),))
    hasta_parar(ej)
    assert len(p.envios) == 1  # un solo intento de mensaje privado
    if ofensivo:  # D3: a un ofensivo nunca se le contesta en público
        assert estado(conn, "c1") == "completado_cerrado" and p.respuestas == []
    else:
        assert estado(conn, "c1") == "completado_cerrado"
        assert p.respuestas == [("c1", "Síguenos y escríbenos ACADEMIA", False)]  # «respuesta_alt», no «Te escribí»
    k = contacto(conn, "ana")
    assert k["contactado"] == 1 and k["resultado"] == "dm_no_entregado" and k["etapa"] == "A"
    assert acciones(conn)[0] == ("dm", "dudoso")
    if ofensivo:
        return
    # Ana escribe por mensaje: la conversación pide el enlace (etapa A) y sale, ahora sí, en el chat que abrió ella.
    from bot import conversacion

    cfg = config.leer_todo(conn)
    d = conversacion.decidir(k["etapa"], False, 0, set(), None, ["INFO"], cfg)
    assert d.respuesta == "dm"
    with conn:
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                     "VALUES (?, 'ana', 'dm', ?, ?, ?)", (k["user_id"], *(ejecutor.iso(AHORA),) * 3))
    p.envio = "enviado"
    hasta_parar(ej)
    assert p.envios[-1][0] == "ana" and "https://academia.com" in p.envios[-1][1]
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "enviado"
    assert contacto(conn, "ana")["resultado"] == "dm_enviado"


def test_otro_comentario_de_quien_no_recibio_el_mensaje_le_vuelve_a_invitar_en_publico(entorno):
    conn, p, r, ej = entorno
    p.envio = "dudoso"
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    from bot import reglas

    k = contacto(conn, "ana")
    assert reglas.tipo_recordatorio(k["etapa"], k["resultado"]) == "respuesta_alt"  # no «ya te mandé la info»


def test_perfil_inexistente_saltado_no_disponible(entorno):
    conn, p, r, ej = entorno
    p.perfiles["borrado"] = InfoPerfil(existe=False, puede_mensaje=False)
    meter_comentario(conn, "c1", "borrado")
    hasta_parar(ej)
    assert estado(conn, "c1") == "saltado_no_disponible"
    assert p.envios == []


# ---------------------------------------------------------------- modo todos


def test_todos_mensaje_y_respuesta_publica(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "ana")
    ej.paso(AHORA)
    assert estado(conn, "c1") == "resp_pendiente"
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado"
    assert p.respuestas == [("c1", "¡Te escribí!", False)]
    assert acciones(conn) == [("dm", "ok"), ("respuesta", "ok")]


def test_todos_ofensivo_mensaje_si_publica_no(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "troll", texto="eres un pendejo")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado"
    assert len(p.envios) == 1 and p.respuestas == []


def test_si_falla_la_respuesta_no_se_repite_el_mensaje(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "ana")
    ej.paso(AHORA)
    p.fallo_en, p.fallo = "respuesta", ErrorPantalla("no encuentro el botón responder")
    ej.paso(AHORA)
    assert estado(conn, "c1") == "resp_pendiente"
    p.fallo_en = None
    hasta_parar(ej, AHORA + timedelta(minutes=11))
    assert estado(conn, "c1") == "completado"
    assert len(p.envios) == 1


def test_respuesta_a_comentario_borrado_tras_mensaje_cuenta_como_completado(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    p.respuesta = "no_disponible"
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado"


# ---------------------------------------------------------------- solo_publicas (plan B)


def test_solo_publicas_sin_mensaje(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "solo_publicas")
    meter_comentario(conn, "c1", "ana")
    meter_comentario(conn, "c2", "troll", texto="pendejo")
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado_publica"
    assert estado(conn, "c2") == "saltado_ofensivo"
    assert p.envios == []
    assert contacto(conn, "ana")["resultado"] == "publica"


def test_solo_publicas_comentario_borrado(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "solo_publicas")
    p.respuesta = "no_disponible"
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert estado(conn, "c1") == "saltado_no_disponible"


# ---------------------------------------------------------------- errores y reintentos


def test_error_reintenta_con_esperas_10_30_90_y_luego_error_final(entorno):
    conn, p, r, ej = entorno
    p.fallo_en, p.fallo = "perfil", ErrorPantalla("no carga")
    meter_comentario(conn, "c1", "ana")
    t = AHORA
    ej.paso(t)
    assert estado(conn, "c1") == "error"
    ej.paso(t + timedelta(minutes=9))
    assert estado(conn, "c1") == "error"  # aún no toca
    for espera in (10, 30, 90):
        t += timedelta(minutes=espera, seconds=1)
        ej.paso(t)
    assert estado(conn, "c1") == "error_final"
    fila = conn.execute("SELECT intentos, ultimo_error FROM comentarios WHERE id='c1'").fetchone()
    assert fila["intentos"] == 4 and "no carga" in fila["ultimo_error"]


def test_error_y_luego_bien(entorno):
    conn, p, r, ej = entorno
    p.fallo_en, p.fallo = "perfil", ErrorPantalla("no carga")
    meter_comentario(conn, "c1", "ana")
    ej.paso(AHORA)
    p.fallo_en = None
    hasta_parar(ej, AHORA + timedelta(minutes=10, seconds=1))
    assert estado(conn, "c1") == "completado"


def test_aviso_a_mitad_vuelve_a_cola_sin_contar_intento_y_frena(entorno):
    conn, p, r, ej = entorno
    p.fallo_en, p.fallo = "perfil", AvisoTikTok("demasiado_rapido", "Vas muy rápido", "a.png")
    meter_comentario(conn, "c1", "ana")
    resultado = ej.paso(AHORA)
    assert resultado.freno is not None and resultado.freno.modo == "frenado"
    assert estado(conn, "c1") == "en_cola"
    assert conn.execute("SELECT intentos FROM comentarios WHERE id='c1'").fetchone()[0] == 0
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "frenado"


def test_aviso_en_respuesta_pendiente_la_deja_pendiente(entorno):
    conn, p, r, ej = entorno
    p.perfiles["bea"] = InfoPerfil(existe=True, puede_mensaje=False)
    meter_comentario(conn, "c1", "bea")
    ej.paso(AHORA)
    p.fallo_en, p.fallo = "respuesta", AvisoTikTok("captcha")
    ej.paso(AHORA)
    assert estado(conn, "c1") == "alt_pendiente"
    assert conn.execute("SELECT intentos FROM comentarios WHERE id='c1'").fetchone()[0] == 0


class PublicaSinConfirmar(PantallasFalsas):
    """TikTok publica la respuesta, pero después algo falla (Studio no vacía el cuadro, sale un aviso…)."""

    def __init__(self, fallo):
        super().__init__()
        self.fallo_tras_publicar = fallo

    def responder(self, reel_id, comentario_id, texto, simulado):
        self.respuestas.append((comentario_id, texto, simulado))
        if self.fallo_tras_publicar is not None:
            error, self.fallo_tras_publicar = self.fallo_tras_publicar, None
            error.tras_enviar = True
            raise error
        return "ok"


@pytest.mark.parametrize("fallo", [ErrorPantalla("No se pudo confirmar la respuesta"), AvisoTikTok("demasiado_rapido")])
def test_respuesta_publica_que_falla_tras_publicar_no_se_repite(conn, fallo):
    """Auditoría 26 nov: si el fallo llega DESPUÉS de pulsar publicar, no se sabe si salió: queda para revisar
    («dudoso») y nunca se vuelve a publicar (antes salía hasta 4 veces en público)."""
    config.guardar(conn, "modo_prueba", False)
    config.guardar(conn, "modo_publica", "todos")
    config.guardar(conn, "landing", "https://academia.com")
    with conn:
        for tipo, txt in [("dm", "Hola {nombre}, mira {landing}"), ("respuesta", "¡Te escribí!")]:
            conn.execute("INSERT INTO textos (tipo, contenido) VALUES (?, ?)", (tipo, txt))
    p = PublicaSinConfirmar(fallo)
    ej = ejecutor.Ejecutor(conn, p, RitmoLibre(), Freno(conn), random.Random(0))
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert estado(conn, "c1") == "dudoso"
    hasta_parar(ej, AHORA + timedelta(days=3))  # pasado cualquier reintento o freno
    assert len(p.respuestas) == 1


# ---------------------------------------------------------------- corte en dm_enviando (sin duplicar)


def test_corte_en_dm_enviando_y_el_mensaje_estaba_enviado(entorno):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana", estado="dm_enviando")
    p.ya_enviado = True
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado"
    assert p.envios == []  # nunca reenvía
    assert contacto(conn, "ana")["resultado"] == "dm_enviado"


@pytest.mark.parametrize("comprobacion", [None, False])
def test_corte_en_dm_enviando_sin_poder_confirmar_es_dudoso(entorno, comprobacion):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana", estado="dm_enviando")
    p.ya_enviado = comprobacion
    hasta_parar(ej)
    assert estado(conn, "c1") == "dudoso"
    assert p.envios == []


def test_error_durante_el_envio_queda_en_dm_enviando_y_se_recupera_sin_duplicar(entorno):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana")
    p.fallo_en, p.fallo = "envio", ErrorPantalla("se cerró la pestaña")
    ej.paso(AHORA)
    assert estado(conn, "c1") == "dm_enviando"
    p.fallo_en, p.ya_enviado = None, None
    hasta_parar(ej)
    assert estado(conn, "c1") == "dudoso"
    assert p.envios == []


def test_error_antes_de_pulsar_enviar_se_reintenta_y_no_queda_dudoso(entorno):
    """Caso real del 25 nov: no se abrió el chat. Nada salió → error con reintento, no «dudoso»."""
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana")
    fallo = ErrorPantalla("No se abrió el chat con @ana")
    fallo.sin_enviar = True
    p.fallo_en, p.fallo = "envio", fallo
    ej.paso(AHORA)
    assert estado(conn, "c1") == "error"
    assert conn.execute("SELECT contactado FROM contactos WHERE usuario = 'ana'").fetchone()[0] in (0, None)


def test_captcha_antes_de_pulsar_enviar_vuelve_a_la_cola_y_no_queda_dudoso(entorno):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana")
    aviso = AvisoTikTok("captcha", "TikTok muestra un captcha")
    aviso.sin_enviar = True
    p.fallo_en, p.fallo = "envio", aviso
    ej.paso(AHORA)
    assert estado(conn, "c1") == "en_cola"


# ---------------------------------------------------------------- una vez por persona, ID estable, modo prueba


def test_misma_persona_dos_comentarios_un_solo_mensaje(entorno):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana", hace_min=20)
    meter_comentario(conn, "c2", "ana", reel="r2", hace_min=10)
    hasta_parar(ej)
    assert estado(conn, "c1") == "completado" and estado(conn, "c2") == "saltado_contactado"
    assert len(p.envios) == 1


def test_id_estable_une_a_la_persona_aunque_cambie_de_usuario(entorno):
    conn, p, r, ej = entorno
    p.perfiles["ana_vieja"] = InfoPerfil(existe=True, puede_mensaje=True, user_id="999", nombre="Ana")
    meter_comentario(conn, "c1", "ana_vieja", hace_min=20)
    hasta_parar(ej)
    assert conn.execute("SELECT user_id FROM comentarios WHERE id='c1'").fetchone()[0] == "999"
    # la misma persona con otro @usuario comenta otra vez
    p.perfiles["ana_nueva"] = InfoPerfil(existe=True, puede_mensaje=True, user_id="999", nombre="Ana")
    meter_comentario(conn, "c2", "ana_nueva", hace_min=5)
    hasta_parar(ej)
    assert estado(conn, "c2") == "saltado_contactado"
    assert len(p.envios) == 1
    assert conn.execute("SELECT usuario FROM contactos WHERE user_id='999'").fetchone()[0] == "ana_nueva"


def test_id_estable_une_a_la_persona_sin_perder_etapa_ni_atencion(entorno):
    """Auditoría 26 nov: la persona existe dos veces ('u:ana', atendida por la bandeja con su número, y '999',
    sin nada). Al unirlas por el ID del perfil no se puede perder lo de 'u:ana': ni que fue atendida (si no, se
    le mandaría el enlace otra vez) ni la etapa, el número o que la lleva el equipo."""
    conn, p, r, ej = entorno
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario) VALUES ('999', 'ana_antes')")
    meter_comentario(conn, "c1", "ana", hace_min=20, user_id="u:ana")
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado', contactado_en = '2026-11-20T10:00:00+00:00', "
                     "etapa = 'C', telefono = '8015551234', humano = 1 WHERE user_id = 'u:ana'")
        conn.execute("INSERT INTO leads (user_id, usuario, telefono, creado_en) VALUES ('u:ana', 'ana', '8015551234', ?)",
                     (AHORA.isoformat(),))
    p.perfiles["ana"] = InfoPerfil(existe=True, puede_mensaje=True, user_id="999", nombre="Ana")
    ej._dm(ej._comentario("c1"), config.leer_todo(conn), AHORA)
    assert p.envios == []
    k = conn.execute("SELECT * FROM contactos").fetchall()
    assert [f["user_id"] for f in k] == ["999"]
    assert (k[0]["usuario"], k[0]["contactado"], k[0]["etapa"], k[0]["telefono"], k[0]["humano"]) == ("ana", 1, "C", "8015551234", 1)
    assert conn.execute("SELECT user_id FROM leads").fetchone()[0] == "999"


def test_modo_prueba_no_pulsa_enviar_y_guarda_simulado(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_prueba", True)
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "ana")
    hasta_parar(ej)
    assert p.envios[0][2] is True and p.respuestas[0][2] is True
    assert acciones(conn) == [("dm", "simulado"), ("respuesta", "simulado")]


def test_sin_textos_no_se_toca_el_comentario(entorno):
    conn, p, r, ej = entorno
    conn.execute("UPDATE textos SET activo = 0 WHERE tipo = 'dm'")
    conn.commit()
    meter_comentario(conn, "c1", "ana")
    res = ej.paso(AHORA)
    assert estado(conn, "c1") == "en_cola" and p.envios == []
    assert res.problema and "textos" in res.problema


def test_si_el_ritmo_no_deja_se_espera(entorno):
    conn, p, r, ej = entorno

    class RitmoParado(RitmoLibre):
        def puede(self, tipo, ahora):
            return Decision(False, ahora + timedelta(minutes=5), "pausa")

    ej.ritmo = RitmoParado()
    meter_comentario(conn, "c1", "ana")
    res = ej.paso(AHORA)
    assert not res.trabajo and res.espera_hasta == AHORA + timedelta(minutes=5)
    assert estado(conn, "c1") == "en_cola" and p.envios == []


# ---------------------------------------------------------------- auditoría 26 nov, hallazgo 20: estados de una vez


class CierreDelBot(BaseException):
    """El bot se cierra de golpe (se apaga el ordenador, se mata el proceso…) a mitad de guardar."""


def _cerrar_al_guardar(monkeypatch, ej, estados):
    original = ej._estado

    def estado_que_corta(cid, nuevo, ahora, **extra):
        if nuevo in estados:
            raise CierreDelBot(nuevo)
        return original(cid, nuevo, ahora, **extra)

    monkeypatch.setattr(ej, "_estado", estado_que_corta)


@pytest.mark.parametrize("cerrado", [False, True])
def test_cierre_del_bot_al_guardar_no_deja_el_comentario_a_medias(entorno, monkeypatch, cerrado):
    """Antes: «dm_enviado» (o «dm_cerrado») se guardaba y LUEGO el estado final. Un cierre entre los dos dejaba el
    comentario en ese estado para siempre: nada lo recoge, su respuesta pública no salía y los demás comentarios
    de esa persona tampoco avanzaban."""
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    if cerrado:
        p.perfiles["ana"] = InfoPerfil(existe=True, puede_mensaje=False)
    meter_comentario(conn, "c1", "ana")
    _cerrar_al_guardar(monkeypatch, ej, {"resp_pendiente", "alt_pendiente"})
    with pytest.raises(CierreDelBot):
        ej.paso(AHORA)
    # Nada a medias: ni la acción, ni «atendida», ni un estado intermedio.
    assert estado(conn, "c1") == ("en_cola" if cerrado else "dm_enviando")
    assert acciones(conn) == []
    assert contacto(conn, "ana")["contactado"] == 0
    # El bot vuelve a arrancar: si el mensaje salió, la recuperación lo ve en el chat y no lo repite.
    monkeypatch.undo()
    p.ya_enviado = True
    hasta_parar(ej)
    assert estado(conn, "c1") == ("completado_cerrado" if cerrado else "completado")
    assert len(p.envios) == (0 if cerrado else 1)  # el mensaje privado salió una sola vez
    assert len(p.respuestas) == 1


@pytest.mark.parametrize("a_medias, final", [("dm_enviado", "completado"), ("dm_cerrado", "completado_cerrado")])
def test_comentarios_que_una_version_anterior_dejo_a_medias_siguen_solos(entorno, a_medias, final):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "ana", estado=a_medias)
    meter_comentario(conn, "c2", "ana", hace_min=5)  # otro comentario de la misma persona, esperando
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = ? WHERE usuario = 'ana'", (a_medias,))
    hasta_parar(ej)
    assert estado(conn, "c1") == final
    assert [cid for cid, *_ in p.respuestas][:1] == ["c1"]  # su respuesta pública sale
    assert estado(conn, "c2") != "en_cola"  # ya no se queda bloqueado
    assert p.envios == []  # a quien ya estaba atendida no se le escribe otra vez


def test_respuesta_publica_cierre_al_guardar_no_deja_la_accion_sin_su_estado(entorno, monkeypatch):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana", estado="resp_pendiente")
    _cerrar_al_guardar(monkeypatch, ej, {"completado"})
    with pytest.raises(CierreDelBot):
        ej.paso(AHORA)
    assert acciones(conn) == [] and estado(conn, "c1") == "resp_pendiente"


def test_guardar_junto_dentro_de_otro_no_guarda_a_mitad(conn):
    from bot import db

    with pytest.raises(CierreDelBot):
        with db.junto(conn):
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('a', '', '')")
            with db.junto(conn):  # el de dentro no guarda por su cuenta
                conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('b', '', '')")
            raise CierreDelBot()
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo IN ('a', 'b')").fetchone()[0] == 0
    with db.junto(conn):
        conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('a', '', '')")
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'a'").fetchone()[0] == 1


# ---------------------------------------------------------------- freno de privados (27 nov)


def _comentan(conn, *usuarios, hace_min=10):
    for u in usuarios:
        meter_comentario(conn, f"c-{u}", u, hace_min=hace_min)


def test_tres_privados_que_no_llegan_paran_los_privados_24_h_y_se_contesta_en_publico(entorno):
    """Visto con TikTok real (26 nov): tras varios privados rechazados, TikTok rechazaba todos los siguientes y cada
    intento alargaba el castigo. Tras 3 seguidos que no llegan, no se intenta ninguno más en 24 h."""
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    p.envio = "dudoso"
    _comentan(conn, "ana", "bea", "carla", "dani", "eva")
    avisos = []
    for _ in range(30):
        paso = ej.paso(AHORA)
        if paso.freno is not None:
            avisos.append(paso.freno)
        if not paso.trabajo:
            break
    assert [u for u, *_ in p.envios] == ["ana", "bea", "carla"]  # dani y eva: sin intento de privado
    assert len(avisos) == 1 and avisos[0].alerta and "pausa de 24 h" in avisos[0].alerta.asunto
    assert avisos[0].hasta == AHORA + timedelta(hours=24)
    for u in ("dani", "eva"):  # igual que si TikTok no lo hubiera dejado pasar: público y, si escribe, el enlace
        assert estado(conn, f"c-{u}") == "completado_cerrado"
        k = contacto(conn, u)
        assert k["contactado"] == 1 and k["resultado"] == "dm_no_entregado" and k["etapa"] == "A"
    assert len(p.respuestas) == 5  # todos reciben su respuesta pública
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'pausa_privados'").fetchone()[0] == 1


def test_pasadas_24_h_se_prueba_un_privado_y_si_llega_todo_sigue_normal(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    p.envio = "dudoso"
    _comentan(conn, "ana", "bea", "carla")
    hasta_parar(ej)
    assert len(p.envios) == 3
    luego = AHORA + timedelta(hours=24, minutes=1)
    p.envio = "enviado"
    _comentan(conn, "fer", "gus", hace_min=0)
    with conn:  # comentan justo antes de «luego» (si no, caducarían por antiguos en algunos ajustes)
        conn.execute("UPDATE comentarios SET comentado_en = ? WHERE id IN ('c-fer', 'c-gus')", (iso(luego - timedelta(minutes=5)),))
    hasta_parar(ej, luego)
    assert [u for u, *_ in p.envios][3:] == ["fer", "gus"]
    assert contacto(conn, "fer")["resultado"] == "dm_enviado" and contacto(conn, "gus")["resultado"] == "dm_enviado"


def test_si_la_prueba_tras_la_pausa_tampoco_llega_otra_pausa_de_24_h(entorno):
    conn, p, r, ej = entorno
    p.envio = "dudoso"
    _comentan(conn, "ana", "bea", "carla")
    hasta_parar(ej)
    luego = AHORA + timedelta(hours=25)
    _comentan(conn, "fer", "gus", hace_min=0)
    with conn:
        conn.execute("UPDATE comentarios SET comentado_en = ? WHERE id IN ('c-fer', 'c-gus')", (iso(luego - timedelta(minutes=5)),))
    hasta_parar(ej, luego)
    assert [u for u, *_ in p.envios] == ["ana", "bea", "carla", "fer"]  # gus ya no: una sola prueba
    from bot import reglas
    assert reglas.privados_en_pausa(conn, luego) == luego + timedelta(hours=24)


def test_un_privado_que_si_llega_en_medio_no_cuenta_para_la_pausa(entorno):
    conn, p, r, ej = entorno
    from bot import reglas
    with conn:
        for i, res in enumerate(["dudoso", "dudoso", "ok", "dudoso", "dudoso"]):
            conn.execute("INSERT INTO acciones (tipo, comentario_id, resultado, hecho_en) VALUES ('dm', ?, ?, ?)",
                         (f"x{i}", res, iso(AHORA)))
        # Un «no llegó» de una respuesta en la conversación (sin comentario) no cuenta: esa persona escribió primero.
        conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en) VALUES ('dm', 'dudoso', ?)", (iso(AHORA),))
    assert reglas.privados_en_pausa(conn, AHORA) is None
    with conn:
        conn.execute("INSERT INTO acciones (tipo, comentario_id, resultado, hecho_en) VALUES ('dm', 'x9', 'dudoso', ?)",
                     (iso(AHORA),))
    assert reglas.privados_en_pausa(conn, AHORA) == AHORA + timedelta(hours=24)


def test_en_pausa_quien_ya_tenia_el_enlace_y_comenta_en_otro_reel_no_recibe_otro_intento(entorno):
    conn, p, r, ej = entorno
    with conn:
        for i in range(3):
            conn.execute("INSERT INTO acciones (tipo, comentario_id, resultado, hecho_en) VALUES ('dm', ?, 'dudoso', ?)",
                         (f"x{i}", iso(AHORA)))
    meter_comentario(conn, "viejo", "ana", reel="r1", estado="completado", hace_min=3 * 24 * 60)
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado', etapa = 'B', contactado_en = ? "
                     "WHERE usuario = 'ana'", (iso(AHORA - timedelta(days=3)),))
    meter_comentario(conn, "nuevo", "ana", reel="r2")
    hasta_parar(ej)
    assert p.envios == []
    assert estado(conn, "nuevo") == "saltado_contactado"
    assert contacto(conn, "ana")["resultado"] == "dm_enviado"  # no se le cambia: sí le llegó en su día


def test_en_pausa_la_bienvenida_a_seguidores_espera(entorno):
    conn, p, r, ej = entorno
    with conn:
        for i in range(3):
            conn.execute("INSERT INTO acciones (tipo, comentario_id, resultado, hecho_en) VALUES ('dm', ?, 'dudoso', ?)",
                         (f"x{i}", iso(AHORA)))
        conn.execute("INSERT INTO contactos (user_id, usuario, etapa) VALUES ('u:nuevo', 'nuevo', 'A')")
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                     "VALUES ('u:nuevo', 'nuevo', 'bienvenida', ?, ?, ?)", (iso(AHORA),) * 3)
    hasta_parar(ej)
    assert p.envios == []
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "pendiente"


# ---------------------------------------------------------------- «Enviar mensaje privado ahora» (Registro)


class RitmoCerrado(RitmoLibre):
    """El ritmo no deja hacer nada ahora (acaba de enviar otro mensaje)."""

    def puede(self, tipo, ahora):
        return Decision(False, ahora + timedelta(minutes=3), "pausa")


def _pedir_a_mano(conn, usuario):
    with conn:
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                     "VALUES (?, ?, 'dm_manual', ?, ?, ?)", (f"u:{usuario}", usuario, *(iso(AHORA),) * 3))


def _no_le_llego(conn, usuario):
    meter_comentario(conn, f"c-{usuario}", usuario, estado="completado_cerrado")
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_no_entregado', etapa = 'A' WHERE usuario = ?",
                     (usuario,))


def test_mensaje_a_mano_sale_ya_sin_esperar_el_ritmo_con_el_texto_de_siempre(entorno):
    conn, p, r, _ = entorno
    ej = ejecutor.Ejecutor(conn, p, RitmoCerrado(), Freno(conn), random.Random(0))
    _no_le_llego(conn, "linda")
    _pedir_a_mano(conn, "linda")
    assert ej.paso(AHORA).trabajo
    assert len(p.envios) == 1 and p.envios[0][0] == "linda"
    assert p.envios[0][1].startswith("Hola") and "https://academia.com" in p.envios[0][1]  # el texto «dm» de Textos
    assert conn.execute("SELECT estado FROM envios").fetchone()[0] == "enviado"
    k = contacto(conn, "linda")
    assert k["resultado"] == "dm_enviado" and k["etapa"] == "B"
    assert ("dm", "ok") in acciones(conn)


def test_mensaje_a_mano_que_tiktok_tampoco_deja_pasar_no_cambia_a_la_persona_ni_cuenta_para_la_pausa(entorno):
    conn, p, r, ej = entorno
    from bot import reglas
    p.envio = "dudoso"
    _no_le_llego(conn, "linda")
    for _ in range(3):
        _pedir_a_mano(conn, "linda")
        ej.paso(AHORA)
    assert len(p.envios) == 3
    assert [f[0] for f in conn.execute("SELECT estado FROM envios")] == ["dudoso"] * 3
    assert contacto(conn, "linda")["resultado"] == "dm_no_entregado"  # sigue igual: si escribe, recibe el enlace
    assert reglas.privados_en_pausa(conn, AHORA) is None


def test_mensaje_a_mano_con_el_enlace_ya_en_el_chat_no_se_repite(entorno):
    conn, p, r, ej = entorno
    _no_le_llego(conn, "linda")
    p.ya_hablados.add("linda")
    _pedir_a_mano(conn, "linda")
    ej.paso(AHORA)
    assert p.envios == []
    estado_envio, error = conn.execute("SELECT estado, ultimo_error FROM envios").fetchone()
    assert estado_envio == "cancelado" and error == "ya tenía el enlace en el chat"


def test_mensaje_a_mano_que_falla_antes_de_enviar_no_se_reintenta_solo(entorno):
    conn, p, r, ej = entorno
    _no_le_llego(conn, "linda")
    error = ErrorPantalla("No aparece el cuadro para escribir")
    error.sin_enviar = True
    p.fallo_en, p.fallo = "envio", error
    _pedir_a_mano(conn, "linda")
    ej.paso(AHORA)
    estado_envio, ultimo = conn.execute("SELECT estado, ultimo_error FROM envios").fetchone()
    assert estado_envio == "error_final" and "cuadro" in ultimo


# ------------------------------------------------------------------ 27 nov: «el bot no hizo nada con persona.dos»
# Visto con TikTok real: a @persona.dos no le llegó el privado (TikTok no lo dejó pasar) y se le contestó en
# público. Al día siguiente, 18 h después, comentó «Me interesa INFO» en un reel NUEVO y el bot no hizo nada:
# ni privado (a quien no le llegó no se le reintentaba) ni comentario (una respuesta pública al día por persona).


def _no_le_llego(conn, usuario, reel="r1", hace=timedelta(hours=18)):
    meter_comentario(conn, f"viejo-{usuario}", usuario, reel=reel, estado="completado_cerrado", hace_min=26 * 60)
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_no_entregado', etapa = 'A', "
                     "contactado_en = ?, ultimo_recordatorio_en = ? WHERE usuario = ?",
                     (iso(AHORA - hace), iso(AHORA - hace), usuario))
        conn.execute("INSERT INTO acciones (tipo, comentario_id, reel_id, resultado, hecho_en) "
                     "VALUES ('respuesta_alt', ?, ?, 'ok', ?)", (f"viejo-{usuario}", reel, iso(AHORA - hace)))


def test_a_quien_no_le_llego_y_comenta_en_otro_reel_se_le_vuelve_a_intentar_el_privado(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    _no_le_llego(conn, "persona.dos")
    meter_comentario(conn, "nuevo", "persona.dos", reel="r2", texto="Me interesa INFO", hace_min=1)
    hasta_parar(ej)
    assert [u for u, *_ in p.envios] == ["persona.dos"]
    assert contacto(conn, "persona.dos")["resultado"] == "dm_enviado"
    assert estado(conn, "nuevo") == "completado"  # privado + su respuesta pública
    assert len(p.respuestas) == 1


def test_si_tampoco_llega_esta_vez_se_le_contesta_en_publico_en_el_reel_nuevo(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    p.envio = "dudoso"
    _no_le_llego(conn, "persona.dos")
    meter_comentario(conn, "nuevo", "persona.dos", reel="r2", texto="Me interesa INFO", hace_min=1)
    hasta_parar(ej)
    assert len(p.envios) == 1
    assert estado(conn, "nuevo") == "completado_cerrado"
    assert len(p.respuestas) == 1  # «escríbenos» en el reel nuevo, aunque no hayan pasado 24 h


def test_en_pausa_de_privados_a_quien_no_le_llego_y_comenta_en_otro_reel_se_le_contesta_en_publico(entorno):
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    with conn:
        for i in range(3):
            conn.execute("INSERT INTO acciones (tipo, comentario_id, resultado, hecho_en) VALUES ('dm', ?, 'dudoso', ?)",
                         (f"x{i}", iso(AHORA)))
    _no_le_llego(conn, "persona.dos")
    meter_comentario(conn, "nuevo", "persona.dos", reel="r2", texto="Me interesa INFO", hace_min=1)
    hasta_parar(ej)
    assert p.envios == []  # en pausa: ningún privado
    assert estado(conn, "nuevo") == "completado_cerrado" and len(p.respuestas) == 1


def test_en_el_mismo_reel_sigue_la_respuesta_publica_como_mucho_una_vez_al_dia(entorno):
    """D35 en el mismo reel no cambia: dos «escríbenos» seguidos a la misma persona en el mismo vídeo es spam."""
    conn, p, r, ej = entorno
    config.guardar(conn, "modo_publica", "todos")
    _no_le_llego(conn, "persona.dos")
    meter_comentario(conn, "nuevo", "persona.dos", reel="r1", texto="hola?", hace_min=1)
    hasta_parar(ej)
    assert p.envios == [] and p.respuestas == []
    assert estado(conn, "nuevo") == "saltado_contactado"


def test_rechazo_7195_pausa_los_privados_al_primero_y_se_contesta_en_publico(entorno):
    """27 nov: si TikTok contesta 7195 («no se envió para proteger nuestra comunidad»), no hace falta esperar a 3
    rechazos: cada intento más alarga el castigo. Pausa de 24 h ya, aviso por correo, y todos reciben su
    respuesta pública (D47). La persona queda «no le llegó» para recibir el enlace si escribe."""
    conn, p, r, ej = entorno
    from bot import reglas
    config.guardar(conn, "modo_publica", "todos")
    p.envio = "bloqueado"
    _comentan(conn, "ana", "bea", "carla")
    avisos = []
    for _ in range(30):
        paso = ej.paso(AHORA)
        if paso.freno is not None:
            avisos.append(paso.freno)
        if not paso.trabajo:
            break
    assert [u for u, *_ in p.envios] == ["ana"]  # un solo intento
    assert reglas.privados_en_pausa(conn, AHORA) == AHORA + timedelta(hours=24)
    assert len(avisos) == 1 and "pausa de 24 h" in avisos[0].alerta.asunto
    assert "7195" in avisos[0].alerta.cuerpo or "proteger" in avisos[0].alerta.cuerpo
    assert len(p.respuestas) == 3
    for u in ("ana", "bea", "carla"):
        assert contacto(conn, u)["resultado"] == "dm_no_entregado"
    assert conn.execute("SELECT resultado FROM acciones WHERE tipo = 'dm'").fetchall()[0][0] == "dudoso"


def test_comentar_a_mano_sale_ya_aunque_el_ritmo_diga_que_espere(entorno):
    """Botón «Comentar en público ahora» (27 nov): un clic = un comentario, sin esperar el turno del ritmo."""
    from bot.ritmo import Decision

    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "linda", reel="r2", estado="completado_cerrado")
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_no_entregado' WHERE usuario = 'linda'")
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, comentario_id, creado_en, listo_desde, actualizado_en) "
                     "VALUES ('u:linda', 'linda', 'publica_manual', 'c1', ?, ?, ?)", (iso(AHORA),) * 3)
    r.puede = lambda tipo, ahora: Decision(False, ahora + timedelta(minutes=5), "pausa")
    paso = ej.paso(AHORA)
    assert paso.trabajo
    assert p.respuestas and p.respuestas[0][0] == "c1" and "escríbenos" in p.respuestas[0][1].lower()
    assert conn.execute("SELECT estado FROM envios WHERE tipo = 'publica_manual'").fetchone()[0] == "enviado"
    assert ("respuesta_alt", "ok") in acciones(conn)
    assert estado(conn, "c1") == "completado_cerrado"  # el comentario no cambia de estado
