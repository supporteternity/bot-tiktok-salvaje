"""selectores.py: cada selector CONFIRMADO encuentra su elemento en una página real guardada (fixtures),
con Chrome sin ventana, sin scripts y sin red (no se visita TikTok)."""

import gzip
import re
from pathlib import Path

import pytest

from bot import instalador as instalador_bot
from bot import selectores as S

FIX = Path(__file__).parent / "fixtures"
hay_chrome = instalador_bot.hay_chrome()  # Mac, Windows (Program Files / LOCALAPPDATA) y Linux

# En qué fixture real se vio cada selector confirmado (y en cuál NO debe estar, si aplica).
DONDE = {
    "boton_iniciar_sesion": ("perfil_sin_sesion", "perfil_con_sesion"),
    "ventana_iniciar_sesion": ("perfil_sin_sesion_con_modal", "perfil_sin_sesion"),
    "codigo_qr": ("perfil_sin_sesion_con_modal", "perfil_con_sesion"),
    "icono_perfil_propio": ("perfil_con_sesion", "perfil_sin_sesion"),
    "icono_mensajes": ("chat_con_sesion_vacio", None),
    "perfil_pagina": ("perfil_con_sesion", None),
    "perfil_usuario": ("perfil_con_sesion", None),
    "perfil_nombre": ("perfil_con_sesion", None),
    "boton_mensaje": ("perfil_con_sesion", None),
    "boton_seguir": ("perfil_con_sesion", None),
    "chat_caja": ("chat_con_sesion_vacio", None),
    "chat_usuario": ("chat_con_sesion_vacio", None),
    "chat_lista_mensajes": ("chat_con_sesion_vacio", None),
    "chat_mensaje": ("chat_con_sesion_enviado", "chat_con_sesion_vacio"),
    "chat_mensaje_texto": ("chat_con_sesion_enviado", "chat_con_sesion_vacio"),
    "chat_mensaje_propio": ("chat_con_sesion_enviado", "chat_con_sesion_vacio"),
    "chat_aviso_mensaje": ("chat_con_sesion_enviado", None),
    "chat_zona_escribir": ("chat_con_sesion_vacio", "chat_solicitud_recibida"),
    "chat_editor": ("chat_con_sesion_vacio", "chat_solicitud_recibida"),
    "chat_conversacion": ("chat_con_sesion_enviado", None),
    "chat_conversacion_abierta": ("chat_con_sesion_enviado", None),
    "chat_solicitud_aceptar": ("chat_solicitud_recibida", "chat_con_sesion_enviado"),
    "bandeja_lista": ("chat_con_sesion_enviado", "perfil_con_sesion"),
    "bandeja_chat_nombre": ("chat_con_sesion_enviado", "perfil_con_sesion"),
    "notificaciones_abrir": ("notificaciones_real", "perfil_sin_sesion"),
    "notificaciones_panel": ("notificaciones_real", "perfil_sin_sesion"),
    "notificaciones_seguidores": ("notificaciones_real", "perfil_sin_sesion"),
    "notificaciones_lista": ("notificaciones_real", "perfil_sin_sesion"),
    "notificaciones_aviso": ("notificaciones_real", "perfil_sin_sesion"),
    "notificaciones_aviso_usuario": ("notificaciones_real", "perfil_sin_sesion"),
    "notificaciones_aviso_texto": ("notificaciones_real", "perfil_sin_sesion"),
    "studio_lista_comentarios": ("studio_comentarios", "perfil_con_sesion"),
    "studio_comentario": ("studio_comentarios", "perfil_con_sesion"),
    "studio_buscar": ("studio_comentarios", "perfil_con_sesion"),
    "studio_comentario_responder": ("studio_comentarios", "perfil_con_sesion"),
    "studio_editor_respuesta": ("studio_responder", "studio_comentarios"),
    "reel_abrir_comentarios": ("reel_captcha_puzle", "perfil_con_sesion"),
    "reel_editor_comentario": ("reel_captcha_puzle", "perfil_con_sesion"),
    "reel_boton_publicar": ("reel_captcha_puzle", "perfil_con_sesion"),
}
VALORES = {"cuenta": "cuentaprueba"}


def html(nombre: str) -> str:
    texto = gzip.decompress((FIX / f"{nombre}.html.gz").read_bytes()).decode("utf-8")
    return re.sub(r"<script\b.*?</script>", "", texto, flags=re.S | re.I)


def test_nombres_unicos_y_estados_validos():
    assert len(S.SELECTORES) == len(S._LISTA)
    for s in S._LISTA:
        assert s.estado in (S.CONFIRMADO, S.POR_CALIBRAR)
        assert re.fullmatch(r"P[1-8]", s.pantalla)
        assert "css-" not in s.css, f"{s.nombre}: nunca clases CSS generadas"


def test_todo_confirmado_tiene_fixture_y_lo_no_visto_esta_por_calibrar():
    confirmados = {s.nombre for s in S._LISTA if s.estado == S.CONFIRMADO}
    assert confirmados == set(DONDE)
    # Lo que no se ha podido ver (comentarios del reel, fijar, enviar, chat cerrado) queda POR CALIBRAR.
    pendientes = {s.nombre for s in S.por_calibrar()}
    assert {"reel_comentario", "reel_comentario_responder", "reel_opcion_fijar"} <= pendientes
    assert {"chat_boton_enviar", "chat_cerrado"} <= pendientes
    assert {"bandeja_solicitudes"} <= pendientes  # §17


def test_urls():
    assert S.url_perfil("@persona.uno") == "https://www.tiktok.com/@persona.uno"
    assert S.url_reel("academia", "123") == "https://www.tiktok.com/@academia/video/123"
    assert S.sel("chat_mensaje_propio").con(cuenta="p") == '[data-e2e="dm-new-chat-item"]:has(a[href="/@p"])'


@pytest.mark.skipif(not hay_chrome, reason="Chrome no está instalado")
def test_selectores_confirmados_en_paginas_reales():
    from playwright.sync_api import sync_playwright

    fallos = []
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel="chrome", headless=True)
        pagina = navegador.new_page(java_script_enabled=False)
        pagina.route("**/*", lambda ruta: ruta.abort())  # sin red: nada sale hacia TikTok
        cache: dict[str, str] = {}
        for nombre, (si, no) in DONDE.items():
            css = S.sel(nombre).css.format(**VALORES)
            for fixture, esperado in ((si, True), (no, False)):
                if fixture is None:
                    continue
                pagina.set_content(cache.setdefault(fixture, html(fixture)), wait_until="domcontentloaded")
                if (pagina.locator(css).count() > 0) != esperado:
                    fallos.append(f"{nombre} {'no está' if esperado else 'sobra'} en {fixture}")
        navegador.close()
    assert not fallos, fallos


def test_seguidores_nuevos_de_la_pagina_real():
    """Página real de notificaciones (25 nov): sale @persona.uno con «empezó a seguirte»; los comentarios no cuentan."""
    from bot.pantallas.bandeja import usuarios_que_siguen

    texto = html("notificaciones_real")
    avisos = re.findall(r'data-e2e="inbox-title"[^>]*href="([^"]+)".*?data-e2e="inbox-content"[^>]*>(.*?)</p>', texto, re.S)
    avisos = [(h, re.sub(r"<[^>]+>", " ", t)) for h, t in avisos]
    assert len(avisos) >= 5
    assert usuarios_que_siguen(avisos, "cuentaprueba") == ["persona.uno"]
    assert usuarios_que_siguen([("/@cuentaprueba", "empezó a seguirte")], "cuentaprueba") == []
    assert usuarios_que_siguen([("/@x", "started following you."), ("/@x", "empezó a seguirte")], "a") == ["x"]
