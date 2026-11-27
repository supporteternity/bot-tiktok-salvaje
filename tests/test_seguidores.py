"""Cupo aparte para quien sigue la cuenta (pedido del usuario, 25 nov): el mensaje le llega a la bandeja normal
y TikTok lo vigila menos. Cuando el cupo general se llena, el bot sigue escribiendo a los seguidores; a los
demás los deja en su sitio de la cola (nadie pierde su turno)."""

from bot.pantallas import InfoPerfil
from conftest import AHORA, meter_comentario
from test_estados import acciones, contacto, entorno, estado  # noqa: F401 (entorno es un fixture)


def sigue(nombre="Ana"):
    return InfoPerfil(existe=True, puede_mensaje=True, nombre=nombre, te_sigue=True)


def no_sigue(nombre="Ana"):
    return InfoPerfil(existe=True, puede_mensaje=True, nombre=nombre, te_sigue=False)


def test_con_cupo_todo_igual_y_se_apunta_si_sigue(entorno):
    conn, p, r, ej = entorno
    meter_comentario(conn, "c1", "ana", hace_min=20)
    meter_comentario(conn, "c2", "luis", hace_min=10)
    p.perfiles = {"ana": no_sigue(), "luis": sigue("Luis")}
    ej.paso(AHORA)
    ej.paso(AHORA)
    assert [e[0] for e in p.envios] == ["ana", "luis"]  # orden de llegada
    filas = conn.execute("SELECT seguidor FROM acciones WHERE tipo = 'dm' ORDER BY id").fetchall()
    assert [f[0] for f in filas] == [0, 1]
    assert contacto(conn, "luis")["te_sigue"] == 1 and contacto(conn, "ana")["te_sigue"] == 0


def test_cupo_general_lleno_escribe_a_seguidores_y_guarda_el_sitio_a_los_demas(entorno):
    conn, p, r, ej = entorno
    r.cupos[False] = False
    meter_comentario(conn, "c1", "ana", hace_min=30)
    meter_comentario(conn, "c2", "luis", hace_min=20)
    meter_comentario(conn, "c3", "eva", hace_min=10)
    p.perfiles = {"ana": no_sigue(), "luis": sigue("Luis"), "eva": no_sigue("Eva")}
    ej.paso(AHORA)  # mira a ana: no sigue → se queda en la cola
    assert p.envios == [] and estado(conn, "c1") == "en_cola"
    assert r.hechos == ["perfil"]  # mirar un perfil también va al ritmo elegido
    ej.paso(AHORA)  # luis sigue → mensaje
    assert [e[0] for e in p.envios] == ["luis"] and estado(conn, "c2") == "completado"
    ej.paso(AHORA)  # eva no sigue → se queda
    paso = ej.paso(AHORA)  # ya no queda nadie sin mirar: espera a que se libere el cupo
    assert not paso.trabajo and paso.espera_hasta is not None
    assert estado(conn, "c1") == estado(conn, "c3") == "en_cola"
    assert ("perfil", "no_sigue") in acciones(conn)
    # al día siguiente, con cupo, ana es la primera (no perdió su turno)
    r.cupos[False] = True
    ej.paso(AHORA)
    assert p.envios[-1][0] == "ana"


def test_sin_ningun_cupo_no_mira_perfiles(entorno):
    conn, p, r, ej = entorno
    r.cupos = {False: False, True: False}
    meter_comentario(conn, "c1", "ana")
    paso = ej.paso(AHORA)
    assert not paso.trabajo and p.envios == [] and r.hechos == []


def test_seguidor_con_su_cupo_lleno_usa_el_general(entorno):
    conn, p, r, ej = entorno
    r.cupos[True] = False
    meter_comentario(conn, "c1", "luis")
    p.perfiles = {"luis": sigue("Luis")}
    ej.paso(AHORA)
    assert [e[0] for e in p.envios] == ["luis"]
    assert conn.execute("SELECT seguidor FROM acciones WHERE tipo = 'dm'").fetchone()[0] == 0


def test_si_no_se_sabe_cuenta_como_no_seguidor(entorno):
    conn, p, r, ej = entorno
    r.cupos[False] = False
    meter_comentario(conn, "c1", "ana")
    p.perfiles = {"ana": InfoPerfil(existe=True, puede_mensaje=True, nombre="Ana", te_sigue=None)}
    ej.paso(AHORA)
    assert p.envios == [] and estado(conn, "c1") == "en_cola"
