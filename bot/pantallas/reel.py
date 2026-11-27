"""Página del reel (PLAN-OFICIAL §9 P2, tarea D3): responder en público colgando de un comentario concreto y
publicar + fijar el comentario fijado. En modo de prueba se escribe y se borra: nunca se publica.

POR CALIBRAR (P aún no tiene vídeos): selectores del reel y formato del JSON de comentarios. Lo que se
sabe de la web de TikTok: la lista de comentarios llega en `/api/comment/list/` con
`{"comments": [{"cid", "text", "create_time", "user": {"uid", "unique_id", "nickname"}}], "has_more", "cursor"}`
y publicar responde en `/api/comment/publish/` con `{"status_code": 0, "comment": {"cid", ...}}`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from playwright.sync_api import Locator

from bot.pantallas import ResultadoFijado, ResultadoRespuesta
from bot.pantallas.comun import Contexto, Escucha, escrito_bien, normalizar, teclear, vaciar
from bot.selectores import (API_COMENTARIOS_REEL, API_PUBLICAR_COMENTARIO, JSON_AMBITO, JSON_REEL,
                            RE_JSON_REHIDRATACION, sel, url_reel)

log = logging.getLogger("bot.pantallas.reel")

MAX_CARGAS = 40  # veces que se baja en la lista buscando un comentario antes de rendirse
MAX_REPASO = 150  # cargas como mucho en un repaso completo (~3.000 comentarios)
PREFIJO = 40  # caracteres del comentario para reconocerlo en la página


@dataclass(frozen=True)
class ComentarioLeido:
    id: str
    reel_id: str
    texto: str
    comentado_en: datetime
    user_id: str | None  # ID interno estable
    usuario: str
    nombre: str | None
    reel_publicado: datetime | None = None  # Studio lo trae en item.createTime
    likes: int = 0  # «me gusta» del comentario (D42: orden como «Populares»)
    reel_descripcion: str | None = None  # texto del video (D41: de ahí sale la palabra clave)


def _primero(d: dict, *claves: str):
    for k in claves:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _fecha_unix(valor) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(valor), UTC) if valor not in (None, "") else None
    except (ValueError, TypeError, OverflowError):
        return None


def _entero(valor) -> int:
    try:
        return max(0, int(valor))
    except (ValueError, TypeError):
        return 0


def es_lista_comentarios(datos: dict) -> bool:
    """¿Es una respuesta con lista de comentarios (aunque venga vacía)?"""
    claves = ("comments", "comment_list", "comment_infos")
    return any(k in datos for k in claves) or (isinstance(datos.get("data"), dict) and es_lista_comentarios(datos["data"]))


def leer_api_comentarios(datos: dict, reel_id: str | None = None) -> tuple[list[ComentarioLeido], bool]:
    """(comentarios, hay_más) de una respuesta JSON con lista de comentarios (reel o Studio).
    POR CALIBRAR: acepta los nombres de campo conocidos de la web de TikTok y variantes probables;
    ignora entradas incompletas (sin ID, reel, usuario u hora)."""
    lista = _primero(datos, "comments", "comment_list", "comment_infos") or []
    if isinstance(datos.get("data"), dict) and not lista:
        return leer_api_comentarios(datos["data"], reel_id)
    leidos = []
    for c in lista:
        if not isinstance(c, dict):
            continue
        user = _primero(c, "user", "user_info", "author") or {}
        cid = _primero(c, "cid", "comment_id", "commentId", "id")
        item = c.get("item") if isinstance(c.get("item"), dict) else {}  # Studio: el reel va en item.itemId
        reel = _primero(c, "aweme_id", "item_id", "video_id") or _primero(item, "itemId", "item_id", "id") or reel_id
        usuario = _primero(user, "unique_id", "uniqueId", "username")
        hora = _primero(c, "create_time", "createTime", "comment_time")
        if not (cid and reel and usuario and hora):
            continue
        uid = _primero(user, "uid", "id", "user_id")
        leidos.append(ComentarioLeido(
            id=str(cid), reel_id=str(reel), texto=_primero(c, "text", "content") or "",
            comentado_en=datetime.fromtimestamp(int(hora), UTC),
            user_id=str(uid) if uid else None, usuario=str(usuario).lstrip("@"),
            nombre=_primero(user, "nickname", "nick_name") or None,
            reel_publicado=_fecha_unix(_primero(item, "createTime", "create_time")),
            likes=_entero(_primero(c, "likeCount", "digg_count", "like_count")),
            reel_descripcion=_primero(item, "desc", "description") or None,
        ))
    return leidos, bool(_primero(datos, "has_more", "hasMore"))


def _comentario_en_pagina(ctx: Contexto, usuario: str, texto: str) -> Locator:
    usuario = usuario.strip().lstrip("@")
    item = ctx.loc(sel("reel_comentario")).filter(
        has=ctx.pagina.locator(f'a[href$="/@{usuario}" i]'))
    prefijo = normalizar(texto)[:PREFIJO]
    return item.filter(has_text=prefijo) if prefijo else item


def _abrir_reel(ctx: Contexto, reel_id: str) -> None:
    ctx.ir(url_reel(ctx.cuenta, reel_id))
    # TikTok abre el vídeo en la pestaña «Podría interesarte»: se pulsa el icono de comentarios (sirve en cualquier idioma).
    abrir = ctx.loc(sel("reel_abrir_comentarios"))
    if ctx.aparece(abrir) and not ctx.loc(sel("reel_lista_comentarios")).first.is_visible():
        abrir.first.click()
        ctx.revisar()
    if not ctx.aparece(ctx.loc(sel("reel_lista_comentarios")), ctx.carga_ms):
        ctx.revisar()
        raise ctx.error(f"No carga la lista de comentarios del reel {reel_id}")
    ctx.revisar()


def _buscar(ctx: Contexto, escucha: Escucha, reel_id: str, comentario_id: str, usuario: str, texto: str) -> Locator | None:
    """Baja por la lista hasta ver el comentario. None si TikTok dice que ya no está (lista completa sin él)."""
    visto_en_api, hay_mas = False, True
    for _ in range(MAX_CARGAS):
        item = _comentario_en_pagina(ctx, usuario, texto)
        if item.count():
            return item.first
        for datos in escucha.nuevos_json():
            leidos, hay_mas = leer_api_comentarios(datos, reel_id)
            visto_en_api |= any(c.id == comentario_id for c in leidos)
        if not hay_mas and not visto_en_api:
            return None
        todos = ctx.loc(sel("reel_comentario"))
        if todos.count():
            todos.last.scroll_into_view_if_needed()
        ctx.pagina.mouse.wheel(0, 1200)
        ctx.pagina.wait_for_timeout(ctx.rng.uniform(800, 1600))
        ctx.revisar()
    raise ctx.error(f"No se encontró el comentario {comentario_id} tras {MAX_CARGAS} cargas")


def _publicar(ctx: Contexto, editor: Locator, texto: str) -> dict | None:
    """Pulsa publicar y espera la respuesta del servidor. Devuelve su JSON si TikTok dice que se publicó."""
    try:
        with ctx.pagina.expect_response(lambda r: API_PUBLICAR_COMENTARIO in r.url, timeout=ctx.verificar_ms) as info:
            boton = ctx.loc(sel("reel_boton_publicar"))
            if boton.count() and boton.first.is_visible():
                boton.first.click()
            else:
                ctx.pagina.keyboard.press("Enter")
        datos = info.value.json()
    except Exception as e:  # noqa: BLE001 — sin respuesta del servidor: se mira la página
        log.warning("Sin respuesta de publicar: %s", e)
        return None
    ctx.revisar()
    return datos if isinstance(datos, dict) and datos.get("status_code", 0) == 0 else None


def responder(ctx: Contexto, reel_id: str, comentario_id: str, usuario: str, texto_comentario: str,
              texto: str, simulado: bool) -> ResultadoRespuesta:
    with Escucha(ctx.pagina, API_COMENTARIOS_REEL) as escucha:
        _abrir_reel(ctx, reel_id)
        item = _buscar(ctx, escucha, reel_id, comentario_id, usuario, texto_comentario)
    if item is None:
        return "no_disponible"

    item.hover()
    boton = item.locator(sel("reel_comentario_responder").css)
    if not ctx.aparece(boton):
        raise ctx.error(f"No aparece «Responder» en el comentario {comentario_id}")
    boton.first.click()
    editor = ctx.loc(sel("reel_editor_comentario"))
    if not ctx.aparece(editor):
        raise ctx.error("No aparece el cuadro para escribir la respuesta")
    editor = editor.first
    ctx.revisar()

    teclear(ctx, editor, texto)
    ctx.revisar()
    if normalizar(texto) not in normalizar(editor.inner_text()):  # TikTok antepone «@usuario» al responder
        vaciar(ctx, editor)
        raise ctx.error("El texto escrito en la respuesta no coincide")
    if simulado:
        vaciar(ctx, editor)
        ctx.revisar()
        return "ok"

    if _publicar(ctx, editor, texto) is not None:
        return "ok"
    # Plan B: ¿se ve la respuesta colgando del comentario?
    respuesta = item.locator(sel("reel_comentario").css).filter(
        has=ctx.pagina.locator(f'a[href$="/@{ctx.cuenta}" i]')).filter(has_text=normalizar(texto)[:PREFIJO])
    if ctx.aparece(respuesta, 3_000):
        return "ok"
    raise ctx.error(f"No se pudo confirmar la respuesta al comentario {comentario_id}")


def publicar_fijado(ctx: Contexto, reel_id: str, texto: str, simulado: bool) -> ResultadoFijado:
    _abrir_reel(ctx, reel_id)
    editor = ctx.loc(sel("reel_editor_comentario"))
    if not ctx.aparece(editor):
        raise ctx.error("No aparece el cuadro para comentar en el reel")
    editor = editor.first
    teclear(ctx, editor, texto)
    ctx.revisar()
    if not escrito_bien(editor, texto):
        vaciar(ctx, editor)
        raise ctx.error("El texto escrito en el comentario fijado no coincide")
    if simulado:
        vaciar(ctx, editor)
        ctx.revisar()
        return "fijado"

    if _publicar(ctx, editor, texto) is None:
        raise ctx.error(f"No se pudo confirmar que se publicó el comentario fijado en {reel_id}")

    # Publicado: a partir de aquí, si algo falla NO se vuelve a publicar (resultado "error" → fijar a mano).
    propio = _comentario_en_pagina(ctx, ctx.cuenta, texto)
    if not ctx.aparece(propio, ctx.verificar_ms):
        log.warning("Comentario fijado publicado en %s pero no se ve en la página", reel_id)
        return "error"
    propio = propio.first
    propio.hover()
    try:
        propio.locator(sel("reel_comentario_mas").css).first.click(timeout=ctx.elemento_ms)
        ctx.loc(sel("reel_opcion_fijar")).first.click(timeout=ctx.elemento_ms)
        ctx.revisar()
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo fijar en %s: %s", reel_id, e)
        return "error"
    return "fijado" if ctx.aparece(ctx.loc(sel("reel_fijado_marca")), ctx.verificar_ms) else "error"


def _publicado_en(html: str) -> datetime | None:
    """Fecha de publicación del reel desde el JSON de la página (POR CALIBRAR: webapp.video-detail)."""
    m = RE_JSON_REHIDRATACION.search(html)
    if not m:
        return None
    try:
        item = json.loads(m.group(1))[JSON_AMBITO][JSON_REEL]["itemInfo"]["itemStruct"]
        return datetime.fromtimestamp(int(item["createTime"]), UTC)
    except (ValueError, KeyError, TypeError):
        return None


def leer_todos(ctx: Contexto, reel_id: str) -> tuple[list[ComentarioLeido], datetime | None]:
    """Repaso completo (C2): baja por toda la lista de comentarios del reel leyendo el JSON que carga la página.
    Devuelve (comentarios, fecha de publicación del reel si se pudo leer). Solo lee: no actúa."""
    leidos: dict[str, ComentarioLeido] = {}
    hubo_datos = False
    with Escucha(ctx.pagina, API_COMENTARIOS_REEL) as escucha:
        _abrir_reel(ctx, reel_id)
        publicado = _publicado_en(ctx.pagina.content())
        hay_mas = True
        for vuelta in range(MAX_REPASO):
            ctx.pagina.wait_for_timeout(ctx.rng.uniform(800, 1600))
            for datos in escucha.nuevos_json():
                hubo_datos = True
                lote, hay_mas = leer_api_comentarios(datos, reel_id)
                leidos.update((c.id, c) for c in lote)
            if (hubo_datos and not hay_mas) or (not hubo_datos and vuelta >= 4):
                break
            todos = ctx.loc(sel("reel_comentario"))
            if todos.count():
                todos.last.scroll_into_view_if_needed()
            ctx.pagina.mouse.wheel(0, 1200)
            ctx.revisar()
    if not hubo_datos and ctx.loc(sel("reel_comentario")).count():
        raise ctx.error(f"El reel {reel_id} muestra comentarios pero no llegaron sus datos (¿cambió la API?)")
    return list(leidos.values()), publicado
