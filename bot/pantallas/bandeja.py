"""Bandeja de mensajes y notificaciones de seguidores (PLAN-OFICIAL §17, P7 y P8). Solo lee, salvo aceptar una
solicitud de mensajes cuando se pide (y nunca en modo de prueba).

- `leer_lista`: conversaciones de /messages (y, si hay, las de «Solicitudes de mensajes»), sin abrirlas.
- `leer_chat`: abre una conversación, lee el @usuario de la cabecera y sus mensajes (nuestros o de la persona).
- `seguidores_nuevos`: panel de notificaciones → «Seguidores» → @usuarios de los avisos.

CONFIRMADO: lista, conversación, nombre, cabecera y mensajes (fixtures chat_*.html). POR CALIBRAR: la entrada de
solicitudes y la lista de notificaciones (ver selectores.py).
"""

from __future__ import annotations

import logging
import re

from bot.pantallas import ChatEnLista, ChatLeido, MensajeChat
from bot.pantallas.comun import Contexto, normalizar
from bot.selectores import TEXTOS_SEGUIDOR_NUEVO, URL_MENSAJES, sel

log = logging.getLogger("bot.pantallas.bandeja")

MAX_CHATS = 30  # solo los de arriba: la lista va del más reciente al más viejo
MAX_SEGUIDORES = 50


def _ir_a_mensajes(ctx: Contexto) -> None:
    ctx.ir(URL_MENSAJES)
    if not ctx.aparece(ctx.loc(sel("bandeja_lista")), ctx.carga_ms):
        ctx.revisar()
        raise ctx.error("No carga la lista de mensajes")
    ctx.revisar()


def _chats_visibles(ctx: Contexto, solicitud: bool) -> list[ChatEnLista]:
    chats = []
    items = ctx.loc(sel("chat_conversacion"))
    for i in range(min(items.count(), MAX_CHATS)):
        item = items.nth(i)
        conv_id = item.get_attribute("data-conv-id")
        if not conv_id:
            continue
        nombre = item.locator(sel("bandeja_chat_nombre").css)
        chats.append(ChatEnLista(conv_id=conv_id, nombre=normalizar(nombre.first.inner_text()) if nombre.count() else "",
                                 vista_previa=normalizar(item.inner_text()), solicitud=solicitud))
    return chats


def _abrir_solicitudes(ctx: Contexto) -> bool:
    entrada = ctx.loc(sel("bandeja_solicitudes"))
    if not entrada.count() or not entrada.first.is_visible():
        return False
    entrada.first.click()
    ctx.revisar()
    ctx.pagina.wait_for_timeout(ctx.rng.uniform(800, 1500))
    return True


def leer_lista(ctx: Contexto) -> list[ChatEnLista]:
    _ir_a_mensajes(ctx)
    ctx.pagina.wait_for_timeout(ctx.rng.uniform(800, 1500))
    chats = _chats_visibles(ctx, solicitud=False)
    if _abrir_solicitudes(ctx):
        vistos = {c.conv_id for c in chats}
        chats += [c for c in _chats_visibles(ctx, solicitud=True) if c.conv_id not in vistos]
    return chats


def _mensajes(ctx: Contexto) -> tuple[MensajeChat, ...]:
    items = ctx.loc(sel("chat_mensaje"))
    propios = f'a[href="/@{ctx.cuenta}" i]'
    salida = []
    for i in range(items.count()):
        item = items.nth(i)
        texto = item.locator(sel("chat_mensaje_texto").css)
        salida.append(MensajeChat(propio=item.locator(propios).count() > 0,
                                  texto=normalizar(texto.first.inner_text()) if texto.count() else ""))
    return tuple(salida)


def leer_chat(ctx: Contexto, chat: ChatEnLista, aceptar: bool) -> ChatLeido:
    _ir_a_mensajes(ctx)
    if chat.solicitud and not _abrir_solicitudes(ctx):
        raise ctx.error("No se encontró «Solicitudes de mensajes» para abrir una solicitud")
    conv_id = chat.conv_id.replace("\\", "").replace('"', "")
    item = ctx.pagina.locator(f'{sel("chat_conversacion").css}[data-conv-id="{conv_id}"]')
    if not ctx.aparece(item):
        raise ctx.error(f"No aparece la conversación {chat.conv_id} en la lista")
    item.first.click()
    ctx.revisar()
    cabecera = ctx.loc(sel("chat_usuario"))
    if not ctx.aparece(ctx.loc(sel("chat_lista_mensajes")), ctx.carga_ms):
        ctx.revisar()
        raise ctx.error(f"No se abrió la conversación {chat.conv_id}")
    ctx.aparece(ctx.loc(sel("chat_mensaje")), 3_000)
    usuario = None
    if cabecera.count():
        m = re.match(r"^\s*@(\S+)\s*$", cabecera.first.inner_text() or "")
        usuario = m.group(1) if m else None

    aceptada = False
    boton = ctx.loc(sel("chat_solicitud_aceptar"))
    if aceptar and boton.count() and boton.first.is_visible():
        boton.first.click()
        ctx.revisar()
        aceptada = True
        ctx.aparece(ctx.loc(sel("chat_editor")), ctx.elemento_ms)
    mensajes = _mensajes(ctx)
    ctx.revisar()
    return ChatLeido(usuario=usuario, mensajes=mensajes, aceptada=aceptada)


def seguidores_nuevos(ctx: Contexto) -> list[str]:
    if "/messages" not in (ctx.pagina.url or ""):
        _ir_a_mensajes(ctx)  # cualquier página con la barra de arriba sirve; esta ya está mapeada
    abrir = ctx.loc(sel("notificaciones_abrir")).filter(visible=True)  # «Actividad» o la campana, la que se vea
    if not ctx.aparece(abrir):
        raise ctx.error("No aparece el botón de notificaciones («Actividad»)")
    abrir.first.click()
    ctx.revisar()
    pestana = ctx.loc(sel("notificaciones_seguidores")).filter(visible=True)
    if not ctx.aparece(pestana):
        raise ctx.error("No aparece la pestaña «Seguidores» de las notificaciones")
    pestana.first.click()
    ctx.revisar()
    ctx.pagina.wait_for_timeout(ctx.rng.uniform(1200, 2000))
    avisos = ctx.loc(sel("notificaciones_aviso")).filter(visible=True)
    leidos = []
    for i in range(min(avisos.count(), MAX_SEGUIDORES * 3)):
        aviso = avisos.nth(i)
        titulo, texto = aviso.locator(sel("notificaciones_aviso_usuario").css), aviso.locator(sel("notificaciones_aviso_texto").css)
        leidos.append((titulo.first.get_attribute("href") if titulo.count() else None,
                       texto.first.inner_text() if texto.count() else ""))
    ctx.pagina.keyboard.press("Escape")
    ctx.revisar()
    return usuarios_que_siguen(leidos, ctx.cuenta)


def usuarios_que_siguen(avisos: list[tuple[str | None, str]], cuenta: str) -> list[str]:
    """@usuarios de los avisos «empezó a seguirte» (enlace del título, texto del aviso), sin repetir ni la cuenta propia.
    Se mira el texto aunque se esté en la pestaña «Seguidores», por si la pestaña no se llegó a cambiar."""
    usuarios: list[str] = []
    for href, texto in avisos:
        m = re.match(r"^/@([^/?#]+)", href or "")
        if not m or not any(t in normalizar(texto).casefold() for t in TEXTOS_SEGUIDOR_NUEVO):
            continue
        if m.group(1).casefold() != cuenta.casefold() and m.group(1) not in usuarios:
            usuarios.append(m.group(1))
    return usuarios[:MAX_SEGUIDORES]
