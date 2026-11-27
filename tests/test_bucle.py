"""Bucle principal (E3) con pantallas y detector simulados y reloj falso."""

import json
import logging
import random
from datetime import timedelta

import pytest

from bot import alertas, bucle, config, registro
from bot.pantallas import AvisoTikTok
from bot.ritmo import iso
from conftest import AHORA, meter_comentario
from test_estados import PantallasFalsas

# 18:00 UTC = 11:00 en Utah: lejos del descanso diario (02:00–08:00)
T0 = AHORA


class PantallasBucle(PantallasFalsas):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.blancos = 0
        self.autopruebas = 0
        self.autoprueba_ok = True
        self.fijados = []

    def en_blanco(self):
        self.blancos += 1

    def autoprueba(self):
        self.autopruebas += 1
        return self.autoprueba_ok, "todo OK" if self.autoprueba_ok else "falta el botón Mensaje"

    def publicar_fijado(self, reel_id, texto, simulado):
        self.fijados.append((reel_id, texto, simulado))
        return "fijado"


class DetectorFalso:
    def __init__(self):
        self.lecturas = []
        self.aviso = None

    def leer(self, ahora):
        if self.aviso:
            raise self.aviso
        self.lecturas.append(ahora)
        return 0


@pytest.fixture
def b(conn, monkeypatch):
    enviadas = []
    monkeypatch.setattr(alertas, "enviar_alerta", lambda c, a, ahora: enviadas.append(a) or True)
    config.guardar(conn, "modo_prueba", False)
    config.guardar(conn, "landing", "https://academia.com")
    with conn:
        for tipo, txt in [("dm", "Hola {nombre} {landing}"), ("respuesta", "¡Te escribí!"),
                          ("respuesta_alt", "Escríbenos ACADEMIA"), ("fijado", "Escríbenos ACADEMIA por DM")]:
            conn.execute("INSERT INTO textos (tipo, contenido) VALUES (?, ?)", (tipo, txt))
    p, d = PantallasBucle(), DetectorFalso()
    bu = bucle.Bucle(conn, p, d, random.Random(3))
    bu.alertas_enviadas = enviadas
    return bu


def estado_bot(conn):
    return conn.execute("SELECT * FROM estado_bot").fetchone()


def correr(bu, desde, horas):
    """Ejecuta vueltas avanzando el reloj lo que pide el bucle."""
    t = desde
    while t < desde + timedelta(hours=horas):
        espera = bu.vuelta(t)
        assert 0 < espera <= bucle.ESPERA_MAX
        t += timedelta(seconds=espera)
    return t


def test_vuelta_actualiza_latido_y_atiende_comentarios(b, conn):
    meter_comentario(conn, "c1", "ana")
    correr(b, T0, 0.2)
    assert conn.execute("SELECT estado FROM comentarios WHERE id='c1'").fetchone()[0] == "completado"
    e = estado_bot(conn)
    assert e["modo"] == "activo" and e["ultimo_latido"] >= iso(T0)


def test_pausa_desde_el_panel_no_hace_nada(b, conn):
    config.guardar(conn, "orden_pausa", True)
    meter_comentario(conn, "c1", "ana")
    correr(b, T0, 0.5)
    assert b.pantallas.envios == [] and b.detector.lecturas == []
    assert estado_bot(conn)["modo"] == "pausado"
    config.guardar(conn, "orden_pausa", False)
    correr(b, T0 + timedelta(hours=1), 0.2)
    assert len(b.pantallas.envios) == 1


def test_lecturas_respetan_su_ritmo_y_se_apuntan(b, conn):
    correr(b, T0, 2)
    momentos = b.detector.lecturas
    assert 8 <= len(momentos) <= 20  # cada 6–12 min
    assert conn.execute("SELECT COUNT(*) FROM acciones WHERE tipo='lectura'").fetchone()[0] == len(momentos)


def test_aviso_de_tiktok_frena_avisa_y_no_toca_tiktok_durante_la_espera(b, conn):
    b.detector.aviso = AvisoTikTok("demasiado_rapido", "Vas muy rápido", "a.png")
    b.vuelta(T0)
    assert estado_bot(conn)["modo"] == "frenado"
    assert b.alertas_enviadas and b.alertas_enviadas[0].tipo == "frenado"
    b.detector.aviso = None
    meter_comentario(conn, "c1", "ana")
    correr(b, T0 + timedelta(minutes=1), 3)
    assert b.detector.lecturas == [] and b.pantallas.envios == []
    assert b.pantallas.blancos >= 1
    correr(b, T0 + timedelta(hours=24, minutes=1), 0.3)  # pasa la espera de 24 h
    assert estado_bot(conn)["modo"] == "activo" and len(b.pantallas.envios) == 1


def test_captcha_parado_hasta_que_una_persona_reanude(b, conn):
    b.detector.aviso = AvisoTikTok("captcha")
    b.vuelta(T0)
    b.detector.aviso = None
    correr(b, T0, 30)
    assert estado_bot(conn)["modo"] == "parado_revision" and b.detector.lecturas == []


def test_descanso_diario_y_autoprueba_una_vez(b, conn):
    ini, fin = b.ritmo.descanso_diario(T0 + timedelta(days=1))
    b.vuelta(ini + timedelta(minutes=5))
    assert estado_bot(conn)["modo"] == "descansando"
    correr(b, ini + timedelta(minutes=6), 1)
    assert b.pantallas.autopruebas == 1
    ev = conn.execute("SELECT detalle FROM eventos WHERE tipo='autoprueba'").fetchall()
    assert len(ev) == 1 and "OK" in ev[0][0]


def test_autoprueba_fallida_avisa_por_email(b, conn):
    b.pantallas.autoprueba_ok = False
    ini, _ = b.ritmo.descanso_diario(T0 + timedelta(days=1))
    b.vuelta(ini + timedelta(minutes=5))
    assert any("pantallas" in a.asunto.lower() for a in b.alertas_enviadas)


def test_historico_solo_sin_nuevos_y_del_mas_nuevo_al_mas_viejo(b, conn):
    meter_comentario(conn, "h_viejo", "h1", hace_min=60 * 24 * 40, estado="historico")
    meter_comentario(conn, "h_nuevo", "h2", hace_min=60 * 24 * 35, estado="historico")
    meter_comentario(conn, "nuevo", "ana", hace_min=5)
    b.vuelta(T0)
    assert conn.execute("SELECT estado FROM comentarios WHERE id='h_nuevo'").fetchone()[0] == "historico"
    correr(b, T0 + timedelta(seconds=5), 0.5)
    usuarios = [u for u, _, _ in b.pantallas.envios]
    assert usuarios == ["ana", "h2", "h1"]


def test_historico_apagado_no_se_atiende(b, conn):
    config.guardar(conn, "atender_historico", False)
    meter_comentario(conn, "h", "h1", hace_min=60 * 24 * 40, estado="historico")
    correr(b, T0, 0.5)
    assert b.pantallas.envios == []


def test_por_defecto_el_bot_no_fija_comentarios(b, conn):
    meter_comentario(conn, "c1", "ana", reel="r1")
    correr(b, T0, 0.3)
    assert b.pantallas.fijados == [] and len(b.pantallas.envios) == 1


def test_comentario_fijado_antes_que_los_comentarios_del_reel_solo_en_solo_cerrados(b, conn):
    config.guardar(conn, "fijado_auto", True)
    meter_comentario(conn, "c1", "ana", reel="r1")
    b.vuelta(T0)
    assert b.pantallas.fijados == [("r1", "Escríbenos ACADEMIA por DM", False)] and b.pantallas.envios == []
    assert conn.execute("SELECT fijado_estado FROM reels WHERE id='r1'").fetchone()[0] == "fijado"
    assert conn.execute("SELECT COUNT(*) FROM acciones WHERE tipo='fijar'").fetchone()[0] == 1
    correr(b, T0 + timedelta(seconds=1), 0.3)
    assert len(b.pantallas.fijados) == 1 and len(b.pantallas.envios) == 1


def test_en_modo_todos_no_se_fija(b, conn):
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "ana", reel="r1")
    correr(b, T0, 0.3)
    assert b.pantallas.fijados == []


def test_sin_textos_avisa_a_una_persona(b, conn):
    conn.execute("UPDATE textos SET activo = 0 WHERE tipo = 'dm'")
    conn.commit()
    meter_comentario(conn, "c1", "ana")
    correr(b, T0, 0.2)
    assert any(a.tipo == "necesita_persona" and "textos" in a.cuerpo for a in b.alertas_enviadas)
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "falta_configurar"  # no «Trabajando»


def test_un_fallo_inesperado_no_mata_el_bucle(b, conn, monkeypatch):
    monkeypatch.setattr(b.ejecutor, "paso", lambda ahora: 1 / 0)
    config.guardar(conn, "modo_publica", "todos")  # sin comentario fijado: va directo al ejecutor
    meter_comentario(conn, "c1", "ana")
    espera = b.vuelta(T0)
    assert espera > 0
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo='error'").fetchone()[0] == 1


def test_simulacion_6h_con_cola_continua(b, conn):
    for i in range(40):
        meter_comentario(conn, f"c{i}", f"u{i}", hace_min=40 - i)
    correr(b, T0, 6)
    estados = dict(conn.execute("SELECT estado, COUNT(*) FROM comentarios GROUP BY estado").fetchall())
    hechas = conn.execute("SELECT COUNT(*) FROM acciones WHERE tipo='dm'").fetchone()[0]
    assert 6 * 8 * 0.6 <= hechas <= 6 * 8  # nivel 1: 8/h
    assert estados.get("completado", 0) == hechas


# ---------------------------------------------------------------- logs JSON


def test_logs_json_con_rotacion(tmp_path):
    log = registro.configurar(tmp_path / "logs", consola=False)
    logging.getLogger("bot.prueba").info("hola %s", "mundo", extra={"comentario": "c1"})
    for h in log.handlers:
        h.flush()
    linea = json.loads((tmp_path / "logs" / "bot.log").read_text(encoding="utf-8").splitlines()[-1])
    assert linea["mensaje"] == "hola mundo" and linea["nivel"] == "INFO" and linea["comentario"] == "c1"
    rot = next(h for h in log.handlers if hasattr(h, "maxBytes"))
    assert rot.maxBytes == 10 * 1024 * 1024 and rot.backupCount == 5
    registro.limpiar()


def test_apagado_espera_sin_abrir_nada_hasta_que_lo_enciendan(conn):
    from bot import bucle

    config.guardar(conn, "apagado", True)
    vueltas = []

    def dormir(s):
        vueltas.append(s)
        if len(vueltas) == 3:
            config.guardar(conn, "apagado", False)  # alguien pulsa «Encender»

    bucle.esperar_encendido(conn, dormir=dormir)
    assert len(vueltas) == 3
    e = conn.execute("SELECT modo, ultimo_latido FROM estado_bot").fetchone()
    assert e["modo"] == "apagado" and e["ultimo_latido"]


def test_no_apagado_no_espera(conn):
    from bot import bucle

    bucle.esperar_encendido(conn, dormir=lambda s: (_ for _ in ()).throw(AssertionError("no debía esperar")))


def test_dormir_se_despierta_con_un_clic_nuevo_en_el_registro_y_no_con_uno_viejo(conn):
    from bot import bucle
    from bot.ritmo import iso as _iso
    from conftest import AHORA as _AHORA

    def pedir():
        with conn:
            conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                         "VALUES ('u:x', 'x', 'dm_manual', ?, ?, ?)", (_iso(_AHORA),) * 3)

    esperas = []
    bucle.dormir(conn, 10, esperas.append)
    assert sum(esperas) == 10  # nada pedido: duerme todo
    pedir()  # pedido viejo (p. ej. el bot está en pausa): no lo despierta una y otra vez
    esperas = []
    bucle.dormir(conn, 10, esperas.append)
    assert sum(esperas) == 10
    esperas = []

    def esperar_y_clic(segundos):
        esperas.append(segundos)
        if len(esperas) == 2:
            pedir()  # alguien pulsa el botón mientras duerme

    bucle.dormir(conn, 60, esperar_y_clic)
    assert len(esperas) == 2 and sum(esperas) == 2 * bucle.MIRAR_BOTON


def test_cortes_de_red_sueltos_entre_lecturas_buenas_no_paran_el_bot(b, conn):
    """Visto en el bot real (26–27 nov): 4 de 5 «fallos inesperados» fueron páginas que tardaron más de 20 s en
    cargar (red). En una cuenta tranquila no sale ningún mensaje que ponga a cero el contador de «5 errores
    seguidos», así que cortes sueltos durante horas acababan en pausa de 30 min con email (y 3 pausas en 24 h,
    en «parado, necesita a una persona»). Una lectura que sale bien demuestra que el bot funciona: el contador
    vuelve a cero."""
    llamadas = {"n": 0}
    leer = b.detector.leer

    def leer_a_veces(ahora):
        llamadas["n"] += 1
        if llamadas["n"] % 2:
            raise TimeoutError("Page.goto: Timeout 20000ms exceeded.")
        return leer(ahora)

    b.detector.leer = leer_a_veces
    correr(b, T0, 3)
    errores = conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'error'").fetchone()[0]
    assert errores >= 2 * 5  # más de 5 cortes en total…
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'pausa_errores'").fetchone()[0] == 0  # …pero sueltos
    assert estado_bot(conn)["modo"] not in ("frenado", "parado_revision")


def test_cinco_fallos_seguidos_sin_nada_bueno_en_medio_siguen_pausando(b, conn):
    def falla(_ahora):
        raise TimeoutError("Page.goto: Timeout 20000ms exceeded.")

    b.detector.leer = falla
    t = T0
    for _ in range(5):
        t += timedelta(seconds=b.vuelta(t))
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'pausa_errores'").fetchone()[0] == 1


def test_si_fallan_los_envios_las_lecturas_buenas_no_tapan_los_errores(b, conn):
    """Lo contrario: si TikTok cambia el chat y TODOS los privados fallan, el bot tiene que pausar y avisar
    aunque entre envío y envío las lecturas de comentarios salgan bien."""
    from bot.pantallas import ErrorPantalla

    config.guardar(conn, "modo_publica", "todos")
    b.pantallas.fallo_en, b.pantallas.fallo = "envio", ErrorPantalla("No aparece el botón «Mensaje»")
    t, i = T0, 0
    while t < T0 + timedelta(hours=4):  # cuenta tranquila: un comentario nuevo cada 15 min
        if t >= T0 + timedelta(minutes=15 * i):
            meter_comentario(conn, f"c{i}", f"u{i}", hace_min=0)
            i += 1
        t += timedelta(seconds=b.vuelta(t))
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'pausa_errores'").fetchone()[0] >= 1


def test_cada_accion_apunta_cuanto_tardo(b, conn, monkeypatch):
    """Hasta el 27 nov la columna `duracion_ms` quedaba siempre vacía: en el bot real no se podía medir cuánto
    tarda un privado, una respuesta o una lectura. Ahora cada acción guarda sus milisegundos."""
    import time

    reloj = {"t": 1000.0}

    def avanza():
        reloj["t"] += 1.5
        return reloj["t"]

    monkeypatch.setattr(time, "monotonic", avanza)
    config.guardar(conn, "modo_publica", "todos")
    meter_comentario(conn, "c1", "ana")
    correr(b, T0, 1)
    filas = conn.execute("SELECT tipo, duracion_ms FROM acciones").fetchall()
    tipos = {f["tipo"] for f in filas}
    assert {"lectura", "dm", "respuesta"} <= tipos
    assert all(f["duracion_ms"] and f["duracion_ms"] > 0 for f in filas), [dict(f) for f in filas]
