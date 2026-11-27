from datetime import timedelta

from bot import config, freno
from bot.ritmo import iso
from conftest import AHORA


def estado(conn):
    return conn.execute("SELECT * FROM estado_bot WHERE id = 1").fetchone()


def eventos(conn, tipo):
    return conn.execute("SELECT * FROM eventos WHERE tipo = ? ORDER BY id", (tipo,)).fetchall()


# ---------------------------------------------------------------- los 6 casos de §7.3


def test_1_primer_aviso_para_baja_un_nivel_y_espera_24h(conn):
    config.guardar(conn, "nivel_ritmo", 3)
    r = freno.Freno(conn).aviso("demasiado_rapido", "Estás yendo demasiado rápido", "c.png", AHORA)
    assert r.modo == "frenado" and r.hasta == AHORA + timedelta(hours=24)
    assert config.leer(conn, "nivel_ritmo") == 2
    e = estado(conn)
    assert e["modo"] == "frenado" and e["frenado_hasta"] == iso(AHORA + timedelta(hours=24)) and e["frenos_7d"] == 1
    assert r.alerta.tipo == "frenado" and r.alerta.captura == "c.png"
    assert eventos(conn, "aviso_tiktok")[0]["captura"] == "c.png"


def test_2_segundo_aviso_en_7_dias_espera_48h_y_baja_otro_nivel(conn):
    config.guardar(conn, "nivel_ritmo", 3)
    f = freno.Freno(conn)
    f.aviso("demasiado_rapido", "", None, AHORA - timedelta(days=3))
    r = f.aviso("restringido", "", None, AHORA)
    assert r.modo == "frenado" and r.hasta == AHORA + timedelta(hours=48)
    assert config.leer(conn, "nivel_ritmo") == 1


def test_aviso_de_hace_mas_de_7_dias_no_cuenta(conn):
    f = freno.Freno(conn)
    f.aviso("demasiado_rapido", "", None, AHORA - timedelta(days=8))
    assert f.aviso("demasiado_rapido", "", None, AHORA).hasta == AHORA + timedelta(hours=24)


def test_3_tercer_aviso_en_7_dias_parado_revision(conn):
    f = freno.Freno(conn)
    f.aviso("demasiado_rapido", "", None, AHORA - timedelta(days=5))
    f.aviso("demasiado_rapido", "", None, AHORA - timedelta(days=2))
    r = f.aviso("desconocido", "ventana rara", None, AHORA)
    assert r.modo == "parado_revision" and r.hasta is None
    assert r.alerta.tipo == "necesita_persona"
    assert f.estado(AHORA + timedelta(days=30)).modo == "parado_revision"  # no vuelve solo


def test_4_captcha_o_verificacion_parado_revision_inmediato(conn):
    config.guardar(conn, "nivel_ritmo", 2)
    r = freno.Freno(conn).aviso("captcha", "", "cap.png", AHORA)
    assert r.modo == "parado_revision" and r.alerta.tipo == "necesita_persona"
    assert config.leer(conn, "nivel_ritmo") == 2  # no baja nivel: no es un aviso de ritmo
    assert len(eventos(conn, "captcha")) == 1
    assert freno.Freno(conn).aviso("verificacion", "", None, AHORA).modo == "parado_revision"


def test_5_sesion_cerrada(conn):
    r = freno.Freno(conn).aviso("sesion_cerrada", "", None, AHORA)
    assert r.modo == "sesion_cerrada" and r.alerta.tipo == "necesita_persona"
    assert "Iniciar sesión del bot" in r.alerta.cuerpo
    assert len(eventos(conn, "sesion_cerrada")) == 1


def test_6_cinco_errores_seguidos_pausa_30_min_y_a_la_tercera_revision(conn):
    f = freno.Freno(conn)
    for i in range(4):
        assert f.error_tecnico(f"fallo {i}", None, AHORA) is None
    r = f.error_tecnico("fallo 4", "e.png", AHORA)
    assert r.modo == "frenado" and r.hasta == AHORA + timedelta(minutes=30) and r.alerta.tipo == "frenado"

    t = AHORA + timedelta(hours=1)
    for _ in range(5):
        r = f.error_tecnico("otra vez", None, t)
    assert r.modo == "frenado"
    t += timedelta(hours=1)
    for _ in range(5):
        r = f.error_tecnico("y otra", None, t)
    assert r.modo == "parado_revision"


def test_una_accion_correcta_reinicia_los_errores_seguidos(conn):
    f = freno.Freno(conn)
    for _ in range(4):
        f.error_tecnico("x", None, AHORA)
    f.accion_ok()
    for _ in range(4):
        assert f.error_tecnico("x", None, AHORA) is None


# ---------------------------------------------------------------- vuelta al trabajo


def test_frenado_vuelve_solo_cuando_pasa_la_espera(conn):
    f = freno.Freno(conn)
    f.aviso("demasiado_rapido", "", None, AHORA)
    assert f.estado(AHORA + timedelta(hours=23)).modo == "frenado"
    assert f.estado(AHORA + timedelta(hours=24, seconds=1)).modo == "activo"
    assert estado(conn)["frenado_hasta"] is None
    assert len(eventos(conn, "reanudar")) == 1


def test_reanudar_a_mano_desde_el_panel(conn):
    f = freno.Freno(conn)
    f.aviso("captcha", "", None, AHORA)
    f.reanudar_manual(AHORA + timedelta(hours=2))
    assert f.estado(AHORA + timedelta(hours=2)).modo == "activo"
    assert len(eventos(conn, "reanudar")) == 1


def test_soporte_quita_una_falsa_alarma(conn):
    """Caso real del ensayo (25 nov): una ventana normal de TikTok frenó 24 h. Soporte la quita desde el panel."""
    config.guardar(conn, "nivel_ritmo", 2)
    f = freno.Freno(conn)
    f.aviso("desconocido", "Ventana desconocida", None, AHORA)
    assert f.quitar_falsa_alarma(AHORA + timedelta(minutes=5)) is True
    e = estado(conn)
    assert e["modo"] == "activo" and e["frenado_hasta"] is None and e["frenos_7d"] == 0
    assert config.leer(conn, "nivel_ritmo") == 2  # vuelve al nivel de antes del aviso
    assert eventos(conn, "aviso_tiktok") == [] and len(eventos(conn, "aviso_falso")) == 1
    # Ya no cuenta para «3 avisos en 7 días»: el siguiente aviso real es el primero (24 h).
    assert f.aviso("demasiado_rapido", "", None, AHORA + timedelta(hours=1)).hasta == AHORA + timedelta(hours=25)


def test_quitar_falsa_alarma_solo_si_esta_frenado(conn):
    f = freno.Freno(conn)
    f.aviso("captcha", "", None, AHORA)
    assert f.quitar_falsa_alarma(AHORA) is False
    assert estado(conn)["modo"] == "parado_revision"
