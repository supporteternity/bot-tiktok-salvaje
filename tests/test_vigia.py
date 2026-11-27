"""Vigía (B2): detectar sesión cerrada, cuenta distinta, captcha, avisos y ventanas desconocidas,
sin falsas alarmas en las pantallas normales. Fixtures reales en tests/fixtures (tokens borrados)."""

import gzip
from pathlib import Path

import pytest

from bot.pantallas import vigia

FIX = Path(__file__).parent / "fixtures"


def html(nombre: str) -> str:
    return gzip.decompress((FIX / f"{nombre}.html.gz").read_bytes()).decode("utf-8")


NORMALES_CON_SESION_P = ["perfil_con_sesion", "chat_con_sesion_vacio", "chat_con_sesion_enviado"]


# ---------------------------------------------------------------- sin falsas alarmas


@pytest.mark.parametrize("nombre", NORMALES_CON_SESION_P)
def test_pantallas_normales_con_sesion_no_dan_aviso(nombre):
    assert vigia.analizar(html(nombre), "https://www.tiktok.com/messages", cuenta="cuentaprueba") is None


def test_sin_cuenta_configurada_no_compara_cuenta():
    assert vigia.analizar(html("perfil_con_sesion"), "https://www.tiktok.com/@persona.uno", cuenta="") is None


def test_usuario_en_sesion():
    assert vigia.usuario_en_sesion(html("perfil_con_sesion")) == "cuentaprueba"
    assert vigia.usuario_en_sesion(html("chat_solicitud_recibida")) == "persona.uno"
    assert vigia.usuario_en_sesion(html("perfil_sin_sesion")) is None


# ---------------------------------------------------------------- P6: sesión


@pytest.mark.parametrize("nombre", ["perfil_sin_sesion", "perfil_sin_sesion_con_modal"])
def test_sin_sesion_es_sesion_cerrada(nombre):
    r = vigia.analizar(html(nombre), "https://www.tiktok.com/@persona.uno", cuenta="cuentaprueba")
    assert r is not None and r[0] == "sesion_cerrada"


def test_redireccion_al_login_es_sesion_cerrada():
    r = vigia.analizar("<html><body>hola</body></html>", "https://www.tiktok.com/login?redirect_url=x", cuenta="")
    assert r[0] == "sesion_cerrada"


def test_otra_cuenta_dentro_tambien_para():
    r = vigia.analizar(html("chat_solicitud_recibida"), "https://www.tiktok.com/messages", cuenta="cuentaprueba")
    assert r[0] == "sesion_cerrada" and "persona.uno" in r[1]


# ---------------------------------------------------------------- P5: avisos y captcha (sintéticos hasta tener reales)

BASE = html("perfil_con_sesion")


def con(fragmento: str) -> str:
    return BASE.replace("</body>", fragmento + "</body>")


@pytest.mark.parametrize("fragmento", [
    '<div id="captcha_container"><div>Verifica para continuar</div></div>',
    '<div class="captcha-verify-container">Drag the slider</div>',
    '<iframe src="https://verify.tiktok.com/captcha/page"></iframe>',
])
def test_captcha(fragmento):
    assert vigia.analizar(con(fragmento), "https://www.tiktok.com/", cuenta="cuentaprueba")[0] == "captcha"


@pytest.mark.parametrize("texto, clase", [
    ("Estás yendo demasiado rápido. Tómate un descanso.", "demasiado_rapido"),
    ("You're tapping too fast. Take a break.", "demasiado_rapido"),
    ("Maximum number of attempts reached. Try again later.", "demasiado_rapido"),
    ("Has alcanzado el número máximo de intentos", "demasiado_rapido"),
    ("Esta función está restringida temporalmente", "restringido"),
    ("You can't send messages for now", "restringido"),
    ("Verifica que eres tú", "verificacion"),
    ("Enter the code we sent to your phone to verify it's you", "verificacion"),
])
def test_avisos_por_texto_visible(texto, clase):
    r = vigia.analizar(con(f'<div class="toast">{texto}</div>'), "https://www.tiktok.com/", cuenta="cuentaprueba")
    assert r is not None and r[0] == clase


def test_texto_dentro_de_scripts_no_cuenta():
    oculto = con('<script>var t = "Estás yendo demasiado rápido";</script>')
    assert vigia.analizar(oculto, "https://www.tiktok.com/", cuenta="cuentaprueba") is None


def test_ventana_modal_desconocida_para():
    modal = con('<div role="dialog" aria-modal="true" aria-label="Algo nuevo"><p>¿Seguro?</p></div>')
    r = vigia.analizar(modal, "https://www.tiktok.com/", cuenta="cuentaprueba")
    assert r[0] == "desconocido" and "Algo nuevo" in r[1]


SUBTITULOS = (
    '<div role="dialog" aria-modal="true" class="_TUXModal _TUXModal--width-Compact">'
    '<div data-e2e="desktop-playback-caption-modal"><h2>Subtítulos</h2>'
    '<button aria-label="Close_button"></button></div></div>'
)


def test_ventana_que_se_esta_cerrando_no_cuenta():
    """Caso real (25 nov, ensayo): una ventana sin nombre en plena animación de cierre frenó el bot 24 h."""
    modal = con('<div role="dialog" aria-modal="true" class="_TUXModal _TUXModal--exiting"><p>¿Seguro?</p></div>')
    assert vigia.analizar(modal, "https://www.tiktok.com/messages", cuenta="cuentaprueba") is None


def test_ventana_de_subtitulos_es_conocida():
    assert vigia.analizar(con(SUBTITULOS), "https://www.tiktok.com/messages", cuenta="cuentaprueba") is None


def test_ventana_sin_nombre_toma_el_nombre_de_dentro():
    modal = con('<div role="dialog" aria-modal="true" class="_TUXModal"><div data-e2e="algo-nuevo-modal"></div></div>')
    r = vigia.analizar(modal, "https://www.tiktok.com/", cuenta="cuentaprueba")
    assert r[0] == "desconocido" and "algo-nuevo-modal" in r[1]


def test_panel_de_notificaciones_es_conocido():
    panel = con('<div role="dialog" aria-label="Notifications"></div>')
    assert vigia.analizar(panel, "https://www.tiktok.com/", cuenta="cuentaprueba") is None


def test_un_comentario_de_la_gente_con_esas_palabras_no_es_un_aviso():
    comentarios = con('<div data-e2e="comment-level-1"><span>Vas demasiado rápido 😂 too fast</span></div>'
                      '<p>Esta función está restringida temporalmente (lo dice un vídeo)</p>')
    assert vigia.analizar(comentarios, "https://www.tiktok.com/@academia/video/1", cuenta="cuentaprueba") is None


@pytest.mark.parametrize("contenedor", [
    '<div role="alert">{}</div>', '<div aria-live="polite"><span>{}</span></div>',
    '<div class="TUXTopToast-content">{}</div>', '<div role="dialog" aria-label="Aviso"><p>{}</p></div>',
])
def test_avisos_en_distintos_contenedores(contenedor):
    r = vigia.analizar(con(contenedor.format("Estás yendo demasiado rápido")), "https://www.tiktok.com/", cuenta="cuentaprueba")
    assert r is not None and r[0] == "demasiado_rapido"


def test_comentarios_en_el_panel_de_notificaciones_no_son_avisos():
    panel = con('<div role="dialog" aria-label="Notificaciones"><p>ana comentó: vas demasiado rápido</p></div>')
    assert vigia.analizar(panel, "https://www.tiktok.com/", cuenta="cuentaprueba") is None


def test_mensajes_del_chat_no_son_avisos():
    """La lista de mensajes del chat es aria-live; un mensaje que diga «try again later» no frena."""
    chat = con('<div data-e2e="dm-new-message-list" role="log" aria-live="polite">'
               '<div data-e2e="dm-new-message-text"><p>Si no carga, try again later 🙏</p></div></div>')
    assert vigia.analizar(chat, "https://www.tiktok.com/messages", cuenta="cuentaprueba") is None


# ---------------------------------------------------------------- capturas reales del 25 nov (cuenta P con sesión)


def test_captcha_real_de_puzle_en_la_pagina_del_video():
    assert vigia.analizar(html("reel_captcha_puzle"), "https://www.tiktok.com/@cuentaprueba/video/1",
                          cuenta="cuentaprueba")[0] == "captcha"


def test_studio_comentarios_real_no_da_aviso():
    assert vigia.analizar(html("studio_comentarios"), "https://www.tiktok.com/tiktokstudio/comment",
                          cuenta="cuentaprueba") is None
