"""TODOS los selectores, URLs, claves de JSON y textos de pantalla de TikTok (PLAN-OFICIAL §3 bis.2, §9).

Nada de esto se escribe en otro archivo: si TikTok cambia su web, se toca solo aquí.

Cada selector lleva su estado:
- CONFIRMADO: visto en páginas reales (fixtures en tests/fixtures/ o capturas de A0/B1).
- POR_CALIBRAR: aún no se ha podido ver (TikTok Studio, comentarios del reel, responder, fijar, botón
  de enviar del chat, chat cerrado). Es la mejor suposición; se confirma o corrige en la fase de
  calibración con `scripts/probar_selectores.py` y se cambia aquí su estado.

Orden de preferencia (§3 bis): `data-e2e` → roles/etiquetas accesibles → texto visible. Nunca clases CSS
generadas (cambian en cada versión de TikTok).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Estado = Literal["confirmado", "por_calibrar"]
CONFIRMADO: Estado = "confirmado"
POR_CALIBRAR: Estado = "por_calibrar"


@dataclass(frozen=True)
class Selector:
    nombre: str
    css: str  # selector de Playwright (CSS; `{x}` se rellena con .con(x=...))
    pantalla: str  # P1..P6 de §9
    estado: Estado
    nota: str = ""

    def con(self, **valores: str) -> str:
        return self.css.format(**valores)


def e2e(valor: str) -> str:
    return f'[data-e2e="{valor}"]'


# --------------------------------------------------------------------------------------------- URLs

URL_BASE = "https://www.tiktok.com"
URL_INICIO_SESION = f"{URL_BASE}/login"
URL_MENSAJES = f"{URL_BASE}/messages"
URL_EN_BLANCO = "about:blank"


def url_perfil(usuario: str) -> str:
    return f"{URL_BASE}/@{usuario.strip().lstrip('@')}"


def url_reel(cuenta: str, reel_id: str) -> str:
    return f"{URL_BASE}/@{cuenta.strip().lstrip('@')}/video/{reel_id}"


# POR CALIBRAR: página de comentarios de TikTok Studio (P1). No se ha podido abrir sin sesión.
URL_STUDIO_COMENTARIOS = f"{URL_BASE}/tiktokstudio/comment"

# ------------------------------------------------------------------- datos JSON que trae la página

# CONFIRMADO (B1): JSON incrustado en cada página (perfil, chat, reel).
ID_JSON_REHIDRATACION = "__UNIVERSAL_DATA_FOR_REHYDRATION__"
RE_JSON_REHIDRATACION = re.compile(
    r'<script id="' + ID_JSON_REHIDRATACION + r'"[^>]*>(.*?)</script>', re.S
)
JSON_AMBITO = "__DEFAULT_SCOPE__"
JSON_SESION = "webapp.app-context"  # .user.uniqueId = cuenta con la sesión iniciada (no está sin sesión)
JSON_PERFIL = "webapp.user-detail"  # .userInfo.user.{id, uniqueId, nickname, privateAccount, secUid}
JSON_REEL = "webapp.video-detail"  # POR CALIBRAR: .itemInfo.itemStruct (P no tiene vídeos aún)

# POR CALIBRAR: respuestas JSON que la página pide al servidor (se escuchan con page.on("response")).
# Se reconocen por un trozo de la ruta; el formato de cada una se confirma en la calibración.
API_COMENTARIOS_REEL = "/api/comment/list/"  # lista de comentarios de un reel (web pública)
API_RESPUESTAS_COMENTARIO = "/api/comment/list/reply/"  # respuestas a un comentario
API_STUDIO_COMENTARIOS = "/tiktokstudio/api/web/commentsV2"  # P1: CONFIRMADO 25 nov: {comments: [{commentId, text, createTime, user: {uid, uniqueId, nickname}, item: {itemId}}], hasMore, cursor}
API_PUBLICAR_COMENTARIO = "/api/comment/publish/"  # confirma que una respuesta/fijado se publicó

# --------------------------------------------------------------------------------------- selectores

_LISTA: tuple[Selector, ...] = (
    # P6 · sin sesión (CONFIRMADO en perfil_sin_sesion*.html)
    Selector("boton_iniciar_sesion", e2e("top-login-button"), "P6", CONFIRMADO, "Solo aparece sin sesión"),
    Selector("ventana_iniciar_sesion", e2e("login-modal"), "P6", CONFIRMADO),
    Selector("codigo_qr", e2e("qr-code"), "P6", CONFIRMADO),
    # Comunes con sesión (CONFIRMADO en perfil_con_sesion / chat_*)
    Selector("icono_perfil_propio", e2e("profile-icon"), "P6", CONFIRMADO, "Solo con sesión"),
    Selector("icono_mensajes", e2e("top-dm-icon"), "P4", CONFIRMADO),
    # P3 · perfil de la persona (CONFIRMADO)
    Selector("perfil_pagina", e2e("user-page"), "P3", CONFIRMADO),
    Selector("perfil_usuario", e2e("user-title"), "P3", CONFIRMADO),
    Selector("perfil_nombre", e2e("user-subtitle"), "P3", CONFIRMADO),
    Selector("boton_mensaje", e2e("message-button"), "P3", CONFIRMADO,
             "Sale incluso sin sesión: «se puede escribir» solo se sabe en el chat"),
    Selector("boton_seguir", e2e("follow-button"), "P3", CONFIRMADO),
    # P3 · cuenta inexistente (POR CALIBRAR; el JSON statusCode != 0 es la señal principal)
    Selector("perfil_no_existe", '[data-e2e="user-page"] >> text=/no se pudo encontrar|couldn.t find/i',
             "P3", POR_CALIBRAR, "Plan B: el JSON de la página ya lo dice"),
    # P4 · chat (CONFIRMADO en chat_con_sesion_*.html)
    Selector("chat_caja", e2e("dm-new-chatbox"), "P4", CONFIRMADO),
    Selector("chat_usuario", e2e("chat-uniqueid"), "P4", CONFIRMADO, "Texto «@usuario» del chat abierto"),
    Selector("chat_lista_mensajes", e2e("dm-new-message-list"), "P4", CONFIRMADO, 'role="log"'),
    Selector("chat_mensaje", e2e("dm-new-chat-item"), "P4", CONFIRMADO),
    Selector("chat_mensaje_texto", e2e("dm-new-message-text"), "P4", CONFIRMADO),
    Selector("chat_mensaje_propio", '[data-e2e="dm-new-chat-item"]:has(a[href="/@{cuenta}"])', "P4", CONFIRMADO,
             "El avatar de cada mensaje enlaza al perfil de quien lo envió"),
    Selector("chat_aviso_mensaje", e2e("dm-warning"), "P4", CONFIRMADO,
             "Vacío si el mensaje salió bien; el texto con fallo está POR CALIBRAR"),
    Selector("chat_zona_escribir", e2e("message-input-area"), "P4", CONFIRMADO),
    Selector("chat_editor", '[data-e2e="dm-new-input-editor"] [contenteditable="true"][role="textbox"]', "P4",
             CONFIRMADO),
    Selector("chat_conversacion", e2e("dm-new-conversation-item"), "P4", CONFIRMADO),
    Selector("chat_conversacion_abierta", '[data-e2e="dm-new-conversation-item"][aria-selected="true"]', "P4", CONFIRMADO,
             "Conversación abierta en la lista; su data-conv-id es «0:1:<id de la persona>:<id propio>» (27 nov)"),
    Selector("chat_solicitud_aceptar", e2e("dm-new-stranger-accept"), "P4", CONFIRMADO,
             "Lo ve quien RECIBE una solicitud (C), no el bot"),
    # P7 · bandeja de mensajes (§17 D37). Lista y nombres CONFIRMADOS en chat_*.html; solicitudes POR CALIBRAR.
    Selector("bandeja_lista", e2e("dm-new-conversation-list"), "P7", CONFIRMADO),
    Selector("bandeja_chat_nombre", e2e("dm-new-conversation-nickname"), "P7", CONFIRMADO,
             "Nombre visible; el @usuario solo sale al abrir el chat (chat-uniqueid)"),
    Selector("bandeja_solicitudes", 'text=/^\\s*(solicitudes de mensajes|message requests)/i', "P7", POR_CALIBRAR,
             "Entrada «Solicitudes de mensajes» arriba de la lista: quien no te sigue y escribe primero"),
    # P8 · notificaciones → seguidores (§17 D40). CONFIRMADO 25 nov en la página real (notificaciones_real.html):
    # con el menú lateral el botón visible es «Actividad» (nav-activity); «inbox-icon» está en la página pero oculto.
    Selector("notificaciones_abrir", '[data-e2e="nav-activity"], [data-e2e="inbox-icon"]', "P8", CONFIRMADO,
             "Se pulsa el que esté visible"),
    Selector("notificaciones_panel", e2e("inbox-notifications"), "P8", CONFIRMADO),
    Selector("notificaciones_seguidores", '[data-e2e="inbox-bar"] [data-e2e="followers"]', "P8", CONFIRMADO,
             "Pestaña «Seguidores» del panel de notificaciones"),
    Selector("notificaciones_lista", e2e("inbox-list"), "P8", CONFIRMADO),
    Selector("notificaciones_aviso", e2e("inbox-list-item"), "P8", CONFIRMADO,
             "Cada aviso: inbox-title (enlace /@usuario) + inbox-content («empezó a seguirte.»)"),
    Selector("notificaciones_aviso_usuario", e2e("inbox-title"), "P8", CONFIRMADO),
    Selector("notificaciones_aviso_texto", e2e("inbox-content"), "P8", CONFIRMADO),
    # P4 · enviar y chat cerrado (POR CALIBRAR: el botón solo aparece al escribir; plan: tecla Enter)
    Selector("chat_boton_enviar", '[data-e2e="message-send"], [aria-label="Enviar"], [aria-label="Send"]', "P4",
             POR_CALIBRAR, "Si no aparece, se envía con Enter"),
    Selector("chat_cerrado", '[data-e2e="dm-new-chat-bottom"] >> text=/no puedes enviar|can.t send|cannot send|'
             r'no se puede enviar|only.*friends|solo.*amigos/i', "P4", POR_CALIBRAR,
             "Texto que sale en lugar del editor cuando la persona no acepta mensajes"),
    # P2 · reel (POR CALIBRAR: P aún no tiene vídeos)
    Selector("reel_lista_comentarios", e2e("comment-list"), "P2", POR_CALIBRAR),
    Selector("reel_comentario", e2e("comment-level-1"), "P2", POR_CALIBRAR),
    Selector("reel_comentario_usuario", e2e("comment-username-1"), "P2", POR_CALIBRAR),
    Selector("reel_comentario_responder", e2e("comment-reply-1"), "P2", POR_CALIBRAR),
    Selector("reel_comentario_mas", e2e("comment-more-1"), "P2", POR_CALIBRAR, "Menú «…» del comentario"),
    Selector("reel_abrir_comentarios", e2e("comment-icon"), "P2", CONFIRMADO,
             "La página del vídeo abre «Podría interesarte»: hay que pulsar el icono de comentarios"),
    Selector("reel_editor_comentario", '[data-e2e="comment-input"] [contenteditable="true"]', "P2", CONFIRMADO),
    Selector("reel_boton_publicar", e2e("comment-post"), "P2", CONFIRMADO),
    Selector("reel_opcion_fijar", 'role=menuitem[name=/^(fijar|pin)/i]', "P2", POR_CALIBRAR),
    Selector("reel_fijado_marca", '[data-e2e="comment-level-1"] >> text=/^(fijado|pinned)$/i', "P2", POR_CALIBRAR),
    # P1 · TikTok Studio (CONFIRMADO 25 nov; lista virtual: cada comentario es un Container dentro de Absolute)
    Selector("studio_lista_comentarios", '[data-tt="components_CommentList_Absolute"]', "P1", CONFIRMADO),
    Selector("studio_comentario", '[data-tt="components_CommentList_Absolute"] > [data-tt="components_CommentList_Container"]', "P1", CONFIRMADO),
    Selector("studio_buscar", '[data-tt="components_SearchBar_TUXTextInput"]', "P1", CONFIRMADO,
             "Buscador de Studio: «Busca un comentario o nombre de usuario»"),
    Selector("studio_comentario_responder", 'text=/^(Responder|Reply)$/', "P1", CONFIRMADO,
             "Dentro de un comentario. Al lado está «Eliminar»: el bot NUNCA lo pulsa"),
    Selector("studio_editor_respuesta", '[data-tt="components_CommentInputEditor_FlexRow"] textarea', "P1", CONFIRMADO,
             "Cuadro «Responder al comentario» (textarea) que se abre debajo del comentario"),
    Selector("studio_boton_enviar", '[data-tt="components_CommentInputEditor_TUXButton"]', "P1", POR_CALIBRAR,
             "Aparece cuando ya hay texto. Publicó bien en el primer envío real (25 nov); falta fixture con el botón"),
)

SELECTORES: dict[str, Selector] = {s.nombre: s for s in _LISTA}


def sel(nombre: str) -> Selector:
    return SELECTORES[nombre]


def por_calibrar() -> list[Selector]:
    return [s for s in _LISTA if s.estado == POR_CALIBRAR]


# ---------------------------------------------------------------------------------- textos (vigía)

# CONFIRMADO: marcas que solo están sin sesión (el texto «Iniciar sesión» NO sirve: está en las
# traducciones de todas las páginas).
MARCAS_SIN_SESION = tuple(
    f'data-e2e="{v}"' for v in ("top-login-button", "login-modal", "qr-code")
)

# Ventanas que no son avisos (el panel de notificaciones enseña comentarios de la gente).
# Texto de un aviso de seguidor nuevo (CONFIRMADO en español; el resto, idiomas probables).
TEXTOS_SEGUIDOR_NUEVO = ("empezó a seguirte", "empezo a seguirte", "te sigue", "started following you", "followed you")

# Ventanas de TikTok que no son avisos. «desktop-playback-caption-modal» = ventana de Subtítulos (vista real el 25 nov).
DIALOGOS_CONOCIDOS = ("notificaciones", "notifications", "desktop-playback-caption-modal")

# POR CALIBRAR (salvo «maximum number of attempts», visto en A3): textos de avisos conocidos de TikTok.
# Solo se buscan dentro de zonas de aviso (ver vigia.py).
AVISOS: dict[str, tuple[str, ...]] = {
    "demasiado_rapido": (
        "demasiado rápido", "demasiado rapido", "too fast", "tapping too fast", "slow down",
        "maximum number of attempts", "número máximo de intentos", "numero maximo de intentos",
        "too many requests", "demasiadas solicitudes", "try again later", "inténtalo de nuevo más tarde",
    ),
    "restringido": (
        "restringid", "restricted", "temporarily blocked", "bloqueado temporalmente",
        "can't send messages", "no puedes enviar mensajes", "violat", "infracción", "suspendid", "banned",
    ),
    "verificacion": (
        "verifica que eres tú", "verifica que eres tu", "verify it's you", "verify it’s you",
        "verify your identity", "verifica tu identidad", "código que enviamos", "code we sent",
    ),
}
