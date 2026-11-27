from bot.pantallas import perfil
from test_vigia import html


def test_id_estable_usuario_y_nombre_desde_la_pagina():
    d = perfil.leer_datos(html("perfil_sin_sesion"))
    assert d == perfil.DatosPerfil(existe=True, user_id="7000000000000000001", usuario="cuentaprueba",
                                   nombre="cuentaprueba", privada=False, te_sigue=False)


def test_tambien_con_sesion():
    d = perfil.leer_datos(html("perfil_con_sesion"))
    assert d.existe and d.user_id == "7000000000000000002" and d.usuario == "persona.uno"


def test_cuenta_inexistente():
    pagina = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
              '{"__DEFAULT_SCOPE__":{"webapp.user-detail":{"statusCode":10221,"userInfo":{}}}}</script>')
    assert perfil.leer_datos(pagina) == perfil.DatosPerfil(existe=False)


def test_pagina_sin_datos():
    assert perfil.leer_datos("<html></html>") is None


def _pagina_con_relacion(relacion: int) -> str:
    return ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            '{"__DEFAULT_SCOPE__":{"webapp.user-detail":{"statusCode":0,"userInfo":{"user":'
            f'{{"id":"77","uniqueId":"persona.uno","nickname":"S","relation":{relacion}}}}}}}}}}}</script>')


def test_te_sigue_segun_la_relacion():
    """Visto real el 25 nov: @persona.uno siguió a la cuenta del bot → relation 6 y botón «Seguir también»;
    sin seguir → relation 0 y botón «Seguir»."""
    assert perfil.leer_datos(_pagina_con_relacion(6)).te_sigue is True
    assert perfil.leer_datos(_pagina_con_relacion(0)).te_sigue is False
    assert perfil.leer_datos(_pagina_con_relacion(1)).te_sigue is False  # la cuenta la sigue a ella, no al revés
    assert perfil.leer_datos(html("perfil_con_sesion")).te_sigue is False


def test_boton_seguir_tambien_es_plan_b():
    assert perfil.te_sigue_por_boton("Seguir también") is True
    assert perfil.te_sigue_por_boton("Follow back") is True
    assert perfil.te_sigue_por_boton("Amigos") is True
    assert perfil.te_sigue_por_boton("Seguir") is False
    assert perfil.te_sigue_por_boton("") is None
