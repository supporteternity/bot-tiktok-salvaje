"""TikTok Studio · comentarios (PLAN-OFICIAL §9 P1, tareas C1 y D3).

Leer: `leer_nuevos` (solo lee). Responder en público: `responder` (D3, desde el 25 nov se responde aquí y no
en la página del vídeo, porque abrir los comentarios del vídeo hace salir un captcha; Studio no).

Lee el JSON que la propia página pide al servidor (page.on("response")) bajando por la lista hasta llegar
a un comentario ya guardado (la lista va de más nuevo a más viejo) o al final. Lo que se escape se
recupera en el repaso por reel (C2).

POR CALIBRAR: URL de la página, ruta de su API, formato del JSON y orden de la lista. Sin sesión de
TikTok no se ha podido ver. No hay plan B por HTML hasta ver la página: si no llegan datos, error con
captura (si se repite, el freno para el bot y avisa).
"""

from __future__ import annotations

from collections.abc import Callable

import logging

from bot.pantallas import AvisoTikTok, ErrorPantalla, ResultadoRespuesta
from bot.pantallas.comun import Contexto, Escucha, normalizar, teclear
from bot.pantallas.reel import ComentarioLeido, es_lista_comentarios, leer_api_comentarios
from bot.selectores import API_PUBLICAR_COMENTARIO, API_STUDIO_COMENTARIOS, URL_STUDIO_COMENTARIOS, sel

log = logging.getLogger("bot.pantallas.studio")

MAX_CARGAS = 30
MAX_REPASO = 150  # repaso completo: ~1.500 comentarios como mucho
PREFIJO = 40  # caracteres del comentario para reconocerlo en la página


def leer_nuevos(ctx: Contexto, conocido: Callable[[str], bool], max_cargas: int = MAX_CARGAS) -> list[ComentarioLeido]:
    """Comentarios de Studio desde el más nuevo hasta el primero ya conocido (incluido el lote donde aparece).
    Para el repaso completo se pasa `conocido` que siempre dice que no y más cargas: lee toda la lista."""
    leidos: dict[str, ComentarioLeido] = {}
    hubo_datos = False
    with Escucha(ctx.pagina, API_STUDIO_COMENTARIOS) as escucha:
        ctx.ir(URL_STUDIO_COMENTARIOS)
        if not ctx.aparece(ctx.loc(sel("studio_lista_comentarios")), ctx.carga_ms):
            ctx.revisar()
            raise ctx.error("No carga la lista de comentarios de TikTok Studio")
        ctx.revisar()
        hay_mas, llegado = True, False
        for vuelta in range(max_cargas):
            ctx.pagina.wait_for_timeout(ctx.rng.uniform(800, 1600))
            for datos in escucha.nuevos_json():
                lote, hay_mas_lote = leer_api_comentarios(datos)
                if not lote and not es_lista_comentarios(datos):
                    continue  # otra respuesta de la API de Studio, no la lista
                hubo_datos, hay_mas = True, hay_mas_lote
                leidos.update((c.id, c) for c in lote)
                llegado |= any(conocido(c.id) for c in lote)
            if llegado or (hubo_datos and not hay_mas) or (not hubo_datos and vuelta >= 4):
                break
            ultimo = ctx.loc(sel("studio_comentario"))
            if ultimo.count():
                ultimo.last.scroll_into_view_if_needed()
            ctx.pagina.mouse.wheel(0, 1200)
            ctx.revisar()
    if not hubo_datos:
        raise ctx.error("TikTok Studio no envió la lista de comentarios (¿cambió la página o su API?)")
    return list(leidos.values())


def _item(ctx: Contexto, usuario: str, texto: str):
    usuario = usuario.strip().lstrip("@")
    item = ctx.loc(sel("studio_comentario")).filter(has=ctx.pagina.locator(f'a[href$="/@{usuario}" i]'))
    prefijo = normalizar(texto)[:PREFIJO]
    return item.filter(has_text=prefijo) if prefijo else item


def responder(ctx: Contexto, comentario_id: str, usuario: str, texto_comentario: str, texto: str,
              simulado: bool) -> ResultadoRespuesta:
    """Responde en público a un comentario desde Studio. Baja por la lista hasta verlo (lo reconoce por
    @usuario y texto), pulsa «Responder», escribe a ritmo humano y envía. En modo de prueba escribe y borra.
    "no_disponible" si Studio dice que el comentario ya no está (borrado).
    Como en el chat (auditoría 26 nov): un fallo ANTES de pulsar enviar lleva `sin_enviar` (se puede reintentar);
    uno DESPUÉS lleva `tras_enviar` (pudo publicarse: el ejecutor no la vuelve a publicar)."""
    try:
        return _responder(ctx, comentario_id, usuario, texto_comentario, texto, simulado)
    except (ErrorPantalla, AvisoTikTok) as e:
        if not getattr(e, "tras_enviar", False):
            e.sin_enviar = True
        raise
    except Exception as e:  # noqa: BLE001 — Playwright antes de pulsar enviar (lo de después ya va marcado)
        err = ErrorPantalla(f"Fallo antes de publicar la respuesta al comentario {comentario_id}: {e}")
        err.sin_enviar = True
        raise err from e


def _responder(ctx: Contexto, comentario_id: str, usuario: str, texto_comentario: str, texto: str,
               simulado: bool) -> ResultadoRespuesta:
    visto = False
    with Escucha(ctx.pagina, API_STUDIO_COMENTARIOS) as escucha:
        ctx.ir(URL_STUDIO_COMENTARIOS)
        if not ctx.aparece(ctx.loc(sel("studio_lista_comentarios")), ctx.carga_ms):
            ctx.revisar()
            raise ctx.error("No carga la lista de comentarios de TikTok Studio")
        ctx.revisar()
        # El buscador de Studio NO encuentra por @usuario (probado el 25 nov: «Aún no hay comentarios»):
        # se baja por la lista, que va de más nuevo a más viejo.
        item = _item(ctx, usuario, texto_comentario)
        hay_mas = True
        for _ in range(MAX_REPASO):  # las respuestas pendientes pueden estar muy abajo en cuentas con mucho volumen
            ctx.pagina.wait_for_timeout(ctx.rng.uniform(900, 1600))
            for datos in escucha.nuevos_json():
                lote, hay_mas_lote = leer_api_comentarios(datos)
                if lote or es_lista_comentarios(datos):
                    hay_mas = hay_mas_lote
                    visto |= any(c.id == comentario_id for c in lote)
            if item.count():
                break
            if not hay_mas and not visto:
                return "no_disponible"
            ctx.pagina.mouse.wheel(0, 1200)
            ctx.revisar()
        else:
            raise ctx.error(f"No se encontró en Studio el comentario {comentario_id}")
    item = item.first
    item.hover()
    boton = item.locator(sel("studio_comentario_responder").css)
    if not ctx.aparece(boton):
        raise ctx.error(f"No aparece «Responder» en el comentario {comentario_id}")
    boton.first.click()
    editor = ctx.loc(sel("studio_editor_respuesta"))
    if not ctx.aparece(editor):
        raise ctx.error("No aparece el cuadro para escribir la respuesta en Studio")
    editor = editor.first
    ctx.revisar()

    teclear(ctx, editor, texto)
    ctx.revisar()
    if normalizar(texto) not in normalizar(editor.input_value()):
        editor.fill("")
        raise ctx.error("El texto escrito en la respuesta no coincide")
    if simulado:
        editor.fill("")
        ctx.revisar()
        return "ok"

    enviar = ctx.loc(sel("studio_boton_enviar"))
    if not ctx.aparece(enviar):
        editor.fill("")
        raise ctx.error("No aparece el botón de enviar la respuesta en Studio")
    # CONFIRMADO en el primer envío real (25 nov): Studio publica en /api/comment/publish/ (200).
    try:
        with ctx.pagina.expect_response(lambda r: API_PUBLICAR_COMENTARIO in r.url, timeout=ctx.verificar_ms) as info:
            enviar.first.click()
        respuesta = info.value
        ok_servidor = respuesta.ok
        try:  # TikTok suele contestar 200 con status_code ≠ 0 cuando rechaza (spam, demasiado rápido)
            cuerpo = respuesta.json()
            if isinstance(cuerpo, dict) and cuerpo.get("status_code", 0) not in (0, "0"):
                ok_servidor = False
                log.warning("TikTok rechazó la respuesta: %s", cuerpo.get("status_msg") or cuerpo.get("status_code"))
        except Exception:  # noqa: BLE001 — sin JSON: vale el código HTTP
            pass
        log.info("Respuesta publicada desde Studio: %s %s ok=%s", respuesta.status, respuesta.url.split("?")[0], ok_servidor)
    except Exception as e:  # noqa: BLE001 — sin respuesta reconocible: se mira la página
        log.warning("Sin respuesta reconocible al publicar en Studio: %s", e)
        ok_servidor = None
    # Ya se pulsó enviar. Salvo que TikTok la haya rechazado, pudo publicarse: los fallos van con `tras_enviar`.
    try:
        ctx.revisar()
        # Al publicarse, Studio vacía o cierra el cuadro.
        vacio = (not editor.is_visible()) or normalizar(editor.input_value()) == ""
    except (ErrorPantalla, AvisoTikTok) as e:
        e.tras_enviar = ok_servidor is not False
        raise
    except Exception as e:  # noqa: BLE001
        err = ErrorPantalla(f"Fallo al comprobar la respuesta al comentario {comentario_id}: {e}")
        err.tras_enviar = ok_servidor is not False
        raise err from e
    if ok_servidor is False:
        raise ctx.error(f"TikTok rechazó la respuesta al comentario {comentario_id}")  # no salió: se puede reintentar
    if ok_servidor or vacio:  # el servidor dijo que sí (status_code 0) o Studio vació el cuadro
        return "ok"
    err = ctx.error(f"No se pudo confirmar la respuesta al comentario {comentario_id}")
    err.tras_enviar = True
    raise err
