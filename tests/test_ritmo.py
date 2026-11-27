import random
from datetime import timedelta

import pytest

from bot import config, ritmo
from bot.ritmo import NIVELES, iso
from conftest import AHORA

PASO = timedelta(seconds=10)


def registrar(conn, tipo, cuando, resultado="ok"):
    with conn:
        conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en) VALUES (?, ?, ?)", (tipo, resultado, iso(cuando)))


def simular(conn, horas, tipos=("dm",), semilla=7, desde=AHORA):
    """Cola infinita: en cada paso de 10 s intenta el siguiente tipo de la rotación. Devuelve [(tipo, hora)]."""
    r = ritmo.Ritmo(conn, random.Random(semilla))
    hechas, t, i = [], desde, 0
    while t < desde + timedelta(hours=horas):
        tipo = tipos[i % len(tipos)]
        if r.puede(tipo, t).ok:
            registrar(conn, tipo, t)
            r.hecho(tipo, t)
            hechas.append((tipo, t))
            i += 1
        t += PASO
    return hechas, r


def max_en_ventana(momentos, ventana):
    momentos = sorted(momentos)
    mejor, j = 0, 0
    for i, m in enumerate(momentos):
        while momentos[j] <= m - ventana:
            j += 1
        mejor = max(mejor, i - j + 1)
    return mejor


@pytest.mark.parametrize("nivel", [1, 2, 3, 4, 5, 6])
def test_simulacion_24h_respeta_topes_pausas_y_descansos(conn, nivel):
    config.guardar(conn, "nivel_ritmo", nivel)
    config.guardar(conn, "tope_dm_dia", 10_000)  # aquí se mide la capacidad de cada velocidad, sin el tope diario
    hechas, r = simular(conn, 24)
    momentos = [t for _, t in hechas]
    por_hora, por_dia = NIVELES[nivel]

    assert len(momentos) <= por_dia
    assert max_en_ventana(momentos, timedelta(hours=1)) <= por_hora
    huecos = [(b - a).total_seconds() for a, b in zip(momentos, momentos[1:])]
    assert min(huecos) >= config.leer(conn, "pausa_min_seg")

    # descanso diario: ninguna acción dentro de la ventana
    for dia in (AHORA, AHORA + timedelta(days=1)):
        ini, fin = r.descanso_diario(dia)
        assert (fin - ini) == timedelta(hours=4)
        assert not [m for m in momentos if ini <= m < fin]

    # descansos largos (D20): al menos uno cada 20 acciones, de ≥3 min (10–30 min en el nivel 1)
    minimo = 600 if nivel == 1 else ritmo.DESCANSO_MINIMO_MIN * 60
    largos = [h for h in huecos if h >= minimo]
    assert len(largos) >= len(momentos) // 20

    # capacidad de §2: llega al menos al 85 % del tope del día (≈16 h efectivas)
    assert len(momentos) >= min(por_dia, por_hora * 16) * 0.85


@pytest.mark.parametrize("nivel", [3, 6])
def test_simulacion_24h_no_pasa_del_tope_diario_de_mensajes(conn, nivel):
    config.guardar(conn, "nivel_ritmo", nivel)
    config.guardar(conn, "tope_dm_seguidores_dia", 0)
    hechas, _ = simular(conn, 24)
    assert len(hechas) == 100


def test_topes_por_tipo_75_por_ciento(conn):
    config.guardar(conn, "nivel_ritmo", 4)  # 40/h → máx. 30 mensajes/h y 30 respuestas/h
    hechas, _ = simular(conn, 6, tipos=("dm", "dm", "dm", "respuesta"))
    dms = [t for tipo, t in hechas if tipo == "dm"]
    assert max_en_ventana(dms, timedelta(hours=1)) <= 30


def test_dm_cerrado_no_cuenta_como_accion(conn):
    config.guardar(conn, "nivel_ritmo", 1)
    for i in range(8):
        registrar(conn, "dm", AHORA - timedelta(minutes=50 - i), resultado="cerrado")
    r = ritmo.Ritmo(conn, random.Random(1))
    momento = AHORA + timedelta(hours=10)  # lejos del descanso diario
    registrar(conn, "dm", momento - timedelta(minutes=59), resultado="cerrado")
    assert r.puede("respuesta_alt", momento).ok


def test_al_reiniciar_respeta_la_ultima_accion_guardada(conn):
    t = AHORA + timedelta(hours=10)
    registrar(conn, "dm", t - timedelta(seconds=5))
    r = ritmo.Ritmo(conn, random.Random(1))
    d = r.puede("dm", t)
    assert not d.ok and d.motivo == "pausa"
    assert d.hasta >= t - timedelta(seconds=5) + timedelta(seconds=config.leer(conn, "pausa_min_seg"))


def test_lecturas_cada_6_a_12_min_y_tope_por_hora(conn):
    hechas, _ = simular(conn, 6, tipos=("lectura",))
    momentos = [t for _, t in hechas]
    huecos = [(b - a).total_seconds() / 60 for a, b in zip(momentos, momentos[1:])]
    assert min(huecos) >= 6
    assert max_en_ventana(momentos, timedelta(hours=1)) <= config.leer(conn, "tope_lecturas_hora")


def test_con_actividad_lee_cada_3_a_5_min_y_sin_ella_vuelve_a_6_a_12(conn):
    from conftest import meter_comentario

    r = ritmo.Ritmo(conn, random.Random(3))
    assert not r.hay_actividad(AHORA)
    r.hecho("lectura", AHORA)
    assert timedelta(minutes=6) <= r.proxima_lectura - AHORA <= timedelta(minutes=12)

    meter_comentario(conn, "c1", "ana")  # detectado ahora
    conn.execute("UPDATE comentarios SET detectado_en = ?", (iso(AHORA),))
    conn.commit()
    assert r.hay_actividad(AHORA)
    r.hecho("lectura", AHORA)
    assert timedelta(minutes=3) <= r.proxima_lectura - AHORA <= timedelta(minutes=5)
    assert not r.hay_actividad(AHORA + timedelta(minutes=31))  # pasada la media hora, vuelve a lo normal


def test_reel_recien_publicado_cuenta_como_actividad(conn):
    conn.execute("INSERT INTO reels (id, url, publicado_en) VALUES ('r9', 'u', ?)", (iso(AHORA - timedelta(hours=1)),))
    conn.commit()
    r = ritmo.Ritmo(conn, random.Random(3))
    assert r.hay_actividad(AHORA)
    assert not r.hay_actividad(AHORA + timedelta(hours=3))


def test_descanso_diario_es_estable_y_cae_entre_las_2_y_las_4_de_utah(conn):
    r1, r2 = ritmo.Ritmo(conn, random.Random(1)), ritmo.Ritmo(conn, random.Random(99))
    ini, fin = r1.descanso_diario(AHORA)
    assert (ini, fin) == r2.descanso_diario(AHORA)
    local = ini.astimezone(ritmo.ZONA)
    assert 2 <= local.hour < 4 or (local.hour == 4 and local.minute == 0)


def test_descanso_diario_que_cruza_la_medianoche_sigue_despues_de_las_12(conn):
    """Auditoría 26 nov: con el descanso a las 23:00 (se puede elegir en Ajustes), pasada la medianoche el bot
    miraba ya la ventana del día siguiente y dejaba de descansar a las 00:00 en vez de a las 03:00."""
    from datetime import datetime

    config.guardar(conn, "descanso_diario_inicio", ["23:00", "23:00"])
    r = ritmo.Ritmo(conn, random.Random(0))
    z = ritmo.ZONA
    for momento in (datetime(2026, 11, 26, 23, 30, tzinfo=z), datetime(2026, 11, 27, 1, 0, tzinfo=z),
                    datetime(2026, 11, 27, 2, 59, tzinfo=z)):
        d = r.descansando(momento)
        assert d is not None and d.motivo == "descanso_diario", momento
        assert d.hasta == datetime(2026, 11, 27, 3, 0, tzinfo=z)
    assert r.descansando(datetime(2026, 11, 27, 3, 0, tzinfo=z)) is None


def test_rango_de_inicio_que_cruza_la_medianoche(conn):
    """«Empieza entre las 23:00 y la 01:00»: antes el margen salía negativo y siempre empezaba a las 23:00 en punto."""
    config.guardar(conn, "descanso_diario_inicio", ["23:00", "01:00"])
    inicios = set()
    for dias in range(30):
        ini, fin = ritmo.Ritmo(conn, random.Random(0)).descanso_diario(AHORA + timedelta(days=dias))
        local = ini.astimezone(ritmo.ZONA)
        assert local.hour in (23, 0) or (local.hour == 1 and local.minute == 0)
        assert fin - ini == timedelta(hours=4)
        inicios.add((local.hour, local.minute))
    assert len(inicios) > 5  # al azar dentro del rango, no siempre a la misma hora


@pytest.mark.parametrize("dia", ["2026-03-08", "2026-11-01"])  # cambios de hora de Utah
def test_descanso_diario_dura_4_horas_reales_aunque_cambie_la_hora(conn, dia):
    from datetime import datetime

    config.guardar(conn, "descanso_diario_inicio", ["01:30", "02:30"])
    r = ritmo.Ritmo(conn, random.Random(0))
    ini, fin = r.descanso_diario(datetime.fromisoformat(dia + "T12:00:00+00:00"))
    assert fin - ini == timedelta(hours=4)


# ---------------------------------------------------------------- niveles


def evento(conn, tipo, cuando):
    with conn:
        conn.execute("INSERT INTO eventos (tipo, creado_en) VALUES (?, ?)", (tipo, iso(cuando)))


def test_velocidad_segura_se_guarda_sin_confirmar(conn):
    assert ritmo.elegir_velocidad(conn, 2, False, AHORA) is None
    assert config.leer(conn, "nivel_ritmo") == 2
    assert conn.execute("SELECT nivel_ritmo FROM estado_bot").fetchone()[0] == 2


def test_velocidad_de_riesgo_pide_entender_el_riesgo(conn):
    nivel = config.leer(conn, "nivel_ritmo")
    assert "Entiendo el riesgo" in ritmo.elegir_velocidad(conn, 5, False, AHORA)
    assert config.leer(conn, "nivel_ritmo") == nivel
    assert ritmo.elegir_velocidad(conn, 5, True, AHORA) is None
    assert config.leer(conn, "nivel_ritmo") == 5
    detalle = conn.execute("SELECT detalle FROM eventos WHERE tipo = 'cambio_nivel' ORDER BY id DESC").fetchone()[0]
    assert "riesgo aceptado" in detalle


def test_velocidad_inexistente(conn):
    assert ritmo.elegir_velocidad(conn, 9, True, AHORA) == "Esa velocidad no existe."


def test_empieza_en_la_recomendada():
    assert config.DEFAULTS["nivel_ritmo"] == ritmo.VELOCIDAD_RECOMENDADA == 3


def registrar_dm(conn, cuando, seguidor=False):
    with conn:
        conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en, seguidor) VALUES ('dm', 'ok', ?, ?)",
                     (iso(cuando), int(seguidor)))


def test_tope_de_mensajes_privados_al_dia(conn):
    """Buenas prácticas (25 nov): lo que más vigila TikTok es el total diario de mensajes a quien no te sigue."""
    config.guardar(conn, "nivel_ritmo", 6)
    config.guardar(conn, "tope_dm_dia", 40)
    for i in range(40):
        registrar_dm(conn, AHORA - timedelta(hours=20) + timedelta(minutes=i))
    r = ritmo.Ritmo(conn, random.Random(1))
    d = r.cupo_dm(AHORA, seguidor=False)
    assert not d.ok and d.motivo == "tope_dm_dia"
    assert d.hasta == AHORA - timedelta(hours=20) + timedelta(hours=24, seconds=1)
    assert r.cupo_dm(AHORA, seguidor=True).ok  # los seguidores tienen su propio cupo
    assert r.puede("dm", AHORA).ok  # aún se puede escribir a alguien (a seguidores)
    assert r.puede("respuesta_alt", AHORA).ok  # la respuesta pública (mensajes cerrados) sigue


def test_cupo_de_seguidores_aparte(conn):
    config.guardar(conn, "nivel_ritmo", 6)
    config.guardar(conn, "tope_dm_dia", 40)
    config.guardar(conn, "tope_dm_seguidores_dia", 50)
    for i in range(50):
        registrar_dm(conn, AHORA - timedelta(hours=5) + timedelta(minutes=i), seguidor=True)
    r = ritmo.Ritmo(conn, random.Random(1))
    assert not r.cupo_dm(AHORA, seguidor=True).ok
    assert r.cupo_dm(AHORA, seguidor=False).ok  # los seguidores no gastan el cupo de los demás
    for i in range(40):
        registrar_dm(conn, AHORA - timedelta(hours=4) + timedelta(minutes=i))
    d = r.puede("dm", AHORA)  # los dos cupos llenos: no se puede escribir a nadie
    assert not d.ok and d.motivo == "tope_dm_dia"


def test_cupo_de_seguidores_apagado(conn):
    config.guardar(conn, "tope_dm_seguidores_dia", 0)
    assert not ritmo.Ritmo(conn, random.Random(1)).cupo_dm(AHORA, seguidor=True).ok


def test_tope_diario_por_defecto_es_100():
    assert config.DEFAULTS["tope_dm_dia"] == ritmo.TOPE_DM_RECOMENDADO == 100
    assert config.DEFAULTS["tope_dm_seguidores_dia"] == ritmo.TOPE_SEGUIDORES_RECOMENDADO == 100


def test_tope_diario_alto_pide_entender_el_riesgo(conn):
    assert "Entiendo el riesgo" in ritmo.elegir_velocidad(conn, 3, False, AHORA, tope_dm_dia=250)
    assert config.leer(conn, "tope_dm_dia") == 100
    assert ritmo.elegir_velocidad(conn, 3, True, AHORA, tope_dm_dia=250) is None
    assert config.leer(conn, "tope_dm_dia") == 250
    assert ritmo.elegir_velocidad(conn, 3, False, AHORA, tope_dm_dia=60) is None
    assert ritmo.elegir_velocidad(conn, 3, True, AHORA, tope_dm_dia=77) == "Ese máximo no existe."
