"""PantallasTikTok con Chrome real (sin ventana) contra el «TikTok falso» (tests/falso_tiktok.py).
Nada sale a internet. Comprueba el flujo, la verificación, el modo de prueba y que el vigía frena."""

import random
from pathlib import Path

import pytest

from bot import instalador as instalador_bot
from bot import config, navegador
from bot.pantallas import AvisoTikTok, ErrorPantalla
from bot.pantallas.tiktok import PantallasTikTok
from falso_tiktok import Comentario, FalsoTikTok, Persona
from conftest import meter_comentario

hay_chrome = instalador_bot.hay_chrome()  # Mac, Windows (Program Files / LOCALAPPDATA) y Linux
pytestmark = pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")


@pytest.fixture
def falso():
    return FalsoTikTok(personas={
        "ana": Persona("ana", "111", "Ana López"),
        "cerrada": Persona("cerrada", "222", mensajes="cerrados"),
        "noentrega": Persona("noentrega", "333", mensajes="no_entrega"),
        "sineco": Persona("sineco", "444", mensajes="sin_eco"),
        "vieja": Persona("vieja", "555", previos=1),
        "saludada": Persona("saludada", "556", previos=1, previos_texto="hola prueba"),
        "con_dominio": Persona("con_dominio", "557", previos=1, previos_texto="la clase está en academia.com/clase"),
        "nueva": Persona("nueva", "666", nueva=True),
        "rara": Persona("rara", "777", nueva=True, perfil_en_chat="otra"),
        "pierde": Persona("pierde", "888", mensajes="se_pierde"),
        "bloqueada": Persona("bloqueada", "889", mensajes="bloqueado"),
        # Caso real del 27 nov (@persona.dos): ya hay conversación (un «Hola» nuestro) y aun así la cabecera sale vacía.
        "sin_cabecera": Persona("sin_cabecera", "890", nueva=True, previos=1, previos_texto="Hola"),
        "otra_abierta": Persona("otra_abierta", "891", nueva=True, previos=1, previos_texto="Hola", conv_abierta="ana"),
    })


@pytest.fixture
def tt(conn, falso):
    config.guardar(conn, "velocidad_tecleo_ms", [1, 3])
    nav = navegador.Navegador(visible=False).abrir()
    falso.instalar(nav.contexto)
    try:
        yield PantallasTikTok(nav, conn, random.Random(1), carga_ms=5_000, elemento_ms=2_000, verificar_ms=2_000,
                              asentar_ms=100)
    finally:
        nav.cerrar()


# ------------------------------------------------------------------ perfil


def test_abrir_perfil_lee_id_y_nombre(tt):
    info = tt.abrir_perfil("@ana")
    assert info.existe and info.puede_mensaje
    assert (info.user_id, info.nombre) == ("111", "Ana López")


def test_perfil_inexistente(tt):
    info = tt.abrir_perfil("nadie")
    assert not info.existe and not info.puede_mensaje


# ------------------------------------------------------------------ mensaje privado


def test_envia_dm_escribiendo_y_verifica(tt, falso):
    tt.abrir_perfil("ana")
    texto = "Hola Ana 👋\nMira esto: https://example.com/?utm_source=tiktok · try again later"
    assert tt.enviar_dm("ana", texto, simulado=False) == "enviado"
    assert len(falso.enviados) == 1 and falso.enviados[0]["texto"].startswith("Hola Ana 👋")
    assert "Mira esto" in falso.enviados[0]["texto"]  # el salto de línea no envió a medias


def test_modo_prueba_no_envia_y_deja_el_cuadro_vacio(tt, falso):
    assert tt.enviar_dm("ana", "Hola de prueba", simulado=True) == "enviado"
    assert falso.enviados == []
    assert tt.nav.pagina.locator('[role="textbox"]').inner_text().strip() == ""


def test_dm_cerrado(tt, falso):
    assert tt.enviar_dm("cerrada", "Hola", simulado=False) == "cerrado"
    assert falso.enviados == []


def test_dm_no_entregado_cuenta_como_cerrado(tt):
    assert tt.enviar_dm("noentrega", "Hola", simulado=False) == "cerrado"


def test_dm_sin_confirmacion_es_dudoso(tt, falso):
    assert tt.enviar_dm("sineco", "Hola", simulado=False) == "dudoso"
    assert len(falso.enviados) == 1


def test_dm_que_tiktok_no_guarda_no_cuenta_como_enviado(tt, falso):
    """Caso real del 25–26 nov (@persona.dos, @persona.tres, @persona.cuatro): el mensaje salió en
    pantalla, el bot lo dio por enviado, pero al volver a abrir el chat no estaba. Ahora se comprueba
    reabriendo el chat; si no está, queda «dudoso» (para revisar): no cuenta como enviado y no se reenvía."""
    assert tt.enviar_dm("pierde", "Hola, vi que te interesó nuestra academia", simulado=False) == "dudoso"
    assert len(falso.enviados) == 1


def test_chat_que_carga_despacio_al_reabrir_no_se_reenvia(tt, falso):
    """Auditoría 26 nov: TikTok SÍ lo guardó, pero al reabrir el chat los mensajes tardan más que la espera.
    «No se ve todavía» no es «no llegó»: si se diera por no enviado, el reintento lo mandaría otra vez."""
    falso.personas["ana"].carga_lenta_ms = 4_000  # más que verificar_ms (2 s en estas pruebas)
    assert tt.enviar_dm("ana", "Déjame tu número y te llamamos", simulado=False) == "dudoso"
    assert falso.personas["ana"].guardados == ["Déjame tu número y te llamamos"]


def test_dm_enviado_se_confirma_reabriendo_el_chat(tt, falso):
    assert tt.enviar_dm("ana", "Hola Ana", simulado=False) == "enviado"
    perfiles = [u for u in falso.visitas if u.rstrip("/").endswith("/@ana")]
    assert len(perfiles) == 2  # abrir para escribir + reabrir para comprobar


def test_dm_a_quien_nunca_habias_escrito_cabecera_vacia(tt, falso):
    """Caso real del 25 nov (@persona.dos): conversación nueva, la cabecera sale «@» sin nombre."""
    assert tt.enviar_dm("nueva", "Hola", simulado=False) == "enviado"
    assert len(falso.enviados) == 1


def test_cabecera_vacia_y_otro_perfil_no_escribe_y_se_sabe_que_no_salio(tt, falso):
    with pytest.raises(ErrorPantalla) as e:
        tt.enviar_dm("rara", "Hola", simulado=False)
    assert e.value.sin_enviar is True and falso.enviados == []


def test_cabecera_vacia_pero_chat_con_mensajes_de_otra_conversacion_no_escribe(tt, falso):
    """Si el chat abierto ya tiene mensajes y la conversación seleccionada es de OTRA persona, no se escribe.
    (Hasta el 27 nov se exigía chat vacío; con TikTok real, @persona.dos tenía un «Hola» nuestro y la cabecera
    vacía: ahora se acepta solo si la conversación seleccionada lleva su id.)"""
    falso.personas["nueva"].previos = 1
    falso.personas["nueva"].conv_abierta = "ana"
    with pytest.raises(ErrorPantalla) as e:
        tt.enviar_dm("nueva", "Hola", simulado=False)
    assert e.value.sin_enviar is True and falso.enviados == []


def test_fallo_de_playwright_antes_de_enviar_se_marca_sin_enviar(tt, falso, monkeypatch):
    from bot.pantallas import chat

    def explota(*_a, **_k):
        raise TimeoutError("clic tapado")

    monkeypatch.setattr(chat, "abrir", explota)
    with pytest.raises(ErrorPantalla) as e:
        tt.enviar_dm("ana", "Hola", simulado=False)
    assert e.value.sin_enviar is True


def test_respuesta_rechazada_por_tiktok_con_200_no_cuenta_como_publicada(tt, falso, conn):
    _reel_con(falso, conn)
    falso.publicar_rechaza = True
    with pytest.raises(ErrorPantalla) as e:
        tt.responder("r1", "c44", "Gracias", simulado=False)
    assert e.value.sin_enviar is True  # TikTok dijo que no: no salió, se puede reintentar


def test_respuesta_confirmada_por_tiktok_cuenta_aunque_studio_no_vacie_el_cuadro(tt, falso, conn):
    """Auditoría 26 nov: el servidor dijo que sí (status_code 0). Darla por fallida hacía que se publicara otra vez."""
    _reel_con(falso, conn, n=5)
    falso.studio_no_vacia = True
    assert tt.responder("r1", "c3", "Gracias", simulado=False) == "ok"
    assert len(falso.publicados) == 1


def test_respuesta_sin_contestacion_tras_publicar_no_se_puede_reintentar(tt, falso, conn):
    """Se pulsó enviar y no llegó respuesta (corte): pudo publicarse. Va con tras_enviar → el ejecutor la deja
    «dudosa» y no la vuelve a publicar."""
    _reel_con(falso, conn, n=5)
    falso.publicar_corta = True
    with pytest.raises(ErrorPantalla) as e:
        tt.responder("r1", "c3", "Gracias", simulado=False)
    assert getattr(e.value, "tras_enviar", False) is True
    assert not getattr(e.value, "sin_enviar", False)
    assert len(falso.publicados) == 1


def test_respuesta_que_falla_antes_de_publicar_se_puede_reintentar(tt, falso, conn, monkeypatch):
    from bot.pantallas import studio_comentarios

    def explota(*_a, **_k):
        raise RuntimeError("Timeout 2000ms exceeded")  # como un fallo de Playwright al escribir

    _reel_con(falso, conn, n=5)
    monkeypatch.setattr(studio_comentarios, "teclear", explota)
    with pytest.raises(ErrorPantalla) as e:
        tt.responder("r1", "c3", "Gracias", simulado=False)
    assert e.value.sin_enviar is True and falso.publicados == []


def test_dm_ya_enviado(tt):
    assert tt.dm_ya_enviado("vieja") is True
    assert tt.dm_ya_enviado("ana") is None  # chat vacío: no se puede asegurar → el ejecutor lo deja dudoso
    assert tt.dm_ya_enviado("cerrada") is None


# ------------------------------------------------------------------ reel


def _reel_con(falso, conn, n=45):
    falso.reels["r1"] = [Comentario(f"c{i}", f"user{i}", f"comentario número {i}") for i in range(n)]
    for i in (3, 44):
        meter_comentario(conn, f"c{i}", f"user{i}", reel="r1", texto=f"comentario número {i}")


def test_responder_baja_por_la_lista_y_publica(tt, falso, conn):
    _reel_con(falso, conn)
    assert tt.responder("r1", "c44", "¡Gracias! Escríbenos ACADEMIA", simulado=False) == "ok"
    assert falso.publicados == [{"reel": "r1", "texto": "¡Gracias! Escríbenos ACADEMIA", "responde": True}]


def test_responder_en_modo_prueba_no_publica(tt, falso, conn):
    _reel_con(falso, conn)
    assert tt.responder("r1", "c3", "Gracias", simulado=True) == "ok"
    assert falso.publicados == []


def test_responder_comentario_borrado(tt, falso, conn):
    _reel_con(falso, conn)
    falso.reels["r1"] = [c for c in falso.reels["r1"] if c.cid != "c3"]
    assert tt.responder("r1", "c3", "Gracias", simulado=False) == "no_disponible"
    assert falso.publicados == []


def test_publicar_y_fijar(tt, falso, conn):
    _reel_con(falso, conn, n=3)
    assert tt.publicar_fijado("r1", "Escríbenos ACADEMIA por mensaje", simulado=False) == "fijado"
    assert len(falso.publicados) == 1 and not falso.publicados[0]["responde"]


def test_fijado_sin_confirmacion_no_se_repite(tt, falso, conn):
    _reel_con(falso, conn, n=3)
    falso.publicar_responde = False
    with pytest.raises(ErrorPantalla):
        tt.publicar_fijado("r1", "Escríbenos", simulado=False)


# ------------------------------------------------------------------ vigía, blanco y autoprueba


def test_sin_sesion_el_vigia_frena(tt, falso):
    falso.sesion = None
    with pytest.raises(AvisoTikTok) as e:
        tt.abrir_perfil("ana")
    assert e.value.clase == "sesion_cerrada" and Path(e.value.captura).is_file()


def test_aviso_en_mitad_del_envio_frena(tt, falso):
    tt.abrir_perfil("ana")
    falso.aviso = "Estás yendo demasiado rápido"
    with pytest.raises(AvisoTikTok) as e:
        tt.enviar_dm("ana", "Hola", simulado=False)
    assert e.value.clase == "demasiado_rapido"
    assert falso.enviados == []


def test_en_blanco_y_autoprueba(tt, falso, conn):
    _reel_con(falso, conn, n=3)
    ok, detalle = tt.autoprueba()
    assert ok, detalle
    assert tt.nav.pagina.url == "about:blank"
    assert falso.publicados == [] and falso.enviados == []


def test_respuesta_sin_confirmacion_es_error(tt, falso, conn):
    _reel_con(falso, conn, n=5)
    falso.publicar_responde = False
    with pytest.raises(ErrorPantalla):
        tt.responder("r1", "c3", "Gracias", simulado=False)


def test_responder_desde_studio_no_pulsa_eliminar_ni_abre_el_video(tt, falso, conn):
    _reel_con(falso, conn)
    assert tt.responder("r1", "c44", "Gracias", simulado=False) == "ok"
    assert not any("/eliminado" in v for v in falso.visitas)
    assert not any("/video/" in v for v in falso.visitas)  # la página del vídeo saca captcha: no se abre


# ---------------------------------------------------------------- D36 afinada (prueba real del 26 nov)


def test_si_ya_le_mandamos_un_enlace_no_se_repite(tt, falso):
    for usuario in ("vieja", "con_dominio"):
        assert tt.enviar_dm(usuario, "Hola, mira https://academia.com", simulado=False, solo_si_nuevo=True) == "ya_hablado"
    assert falso.enviados == []


def test_un_saludo_nuestro_sin_enlace_no_impide_mandar_el_enlace(tt, falso):
    """Prueba real del 26 nov: alguien escribió «hola prueba» a mano desde la cuenta; el bot creyó que ya tenía la
    info y no le mandó el enlace."""
    resultado = tt.enviar_dm("saludada", "Hola, mira https://academia.com", simulado=False, solo_si_nuevo=True)
    assert resultado == "enviado"
    assert [e["texto"] for e in falso.enviados] == ["Hola, mira https://academia.com"]


def test_si_tiktok_rechaza_el_privado_con_7195_se_sabe_al_momento(tt, falso):
    """27 nov: TikTok contesta «status_code 7195 / sending_ban» cuando no deja pasar un privado del bot. Antes el bot
    solo lo notaba al volver a abrir el chat (5 s de espera + recarga) y lo dejaba como «dudoso». Ahora lo lee en la
    respuesta del servidor: «bloqueado», sin recargar."""
    assert tt.enviar_dm("bloqueada", "Hola, aquí la info https://academia.com", simulado=False) == "bloqueado"
    assert len(falso.enviados) == 1  # se pulsó enviar una sola vez: nunca se reintenta


def test_un_privado_normal_no_se_confunde_con_un_rechazo(tt, falso):
    assert tt.enviar_dm("ana", "Hola, aquí la info https://academia.com", simulado=False) == "enviado"


def test_cabecera_vacia_con_conversacion_previa_se_reconoce_por_la_conversacion_seleccionada(tt, falso):
    """27 nov, con TikTok real: @persona.dos tenía un «Hola» nuestro y TikTok dejó la cabecera en «@». El bot no se
    fiaba y no escribía nada. La conversación seleccionada lleva el id de la persona: con eso basta."""
    assert tt.enviar_dm("sin_cabecera", "Hola, aquí la info https://academia.com", simulado=False,
                        solo_si_nuevo=True) == "enviado"
    assert len(falso.enviados) == 1


def test_cabecera_vacia_y_seleccionada_la_conversacion_de_otra_persona_no_se_escribe(tt, falso):
    with pytest.raises(ErrorPantalla):
        tt.enviar_dm("otra_abierta", "Hola, aquí la info https://academia.com", simulado=False, solo_si_nuevo=True)
    assert falso.enviados == []
