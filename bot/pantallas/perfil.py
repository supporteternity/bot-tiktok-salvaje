"""Perfil de una persona (PLAN-OFICIAL §9 P3). Lectura de datos desde el JSON que trae la propia página
(más fiable que el HTML): ID interno estable, @usuario, nombre visible y si la cuenta es privada.

`abrir` navega al perfil (D2). El botón «Mensaje» aparece incluso sin sesión, así que «se puede
escribir» solo se sabe en el chat (chat.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from bot.pantallas import InfoPerfil
from bot.selectores import JSON_AMBITO, JSON_PERFIL, sel, url_perfil
from bot.selectores import RE_JSON_REHIDRATACION as _REHIDRATACION



@dataclass(frozen=True)
class DatosPerfil:
    existe: bool
    user_id: str | None = None  # ID interno (no cambia aunque cambie el @usuario)
    usuario: str | None = None
    nombre: str | None = None
    privada: bool = False
    te_sigue: bool | None = None


# Relación con la cuenta del bot en el JSON del perfil. Visto real (25 nov): 6 = la persona sigue a la cuenta
# (botón «Seguir también»); 0 = ninguna. 2 = amigos (se siguen los dos). 1 = la cuenta la sigue, ella no.
RELACION_TE_SIGUE = (2, 6)
BOTONES_TE_SIGUE = ("seguir también", "follow back", "amigos", "amigo/a", "friends")


def te_sigue_por_boton(texto: str) -> bool | None:
    """Plan B si el JSON no trae `relation`: el texto del botón de seguir."""
    t = (texto or "").strip().casefold()
    if not t:
        return None
    return t in BOTONES_TE_SIGUE


def leer_datos(html: str) -> DatosPerfil | None:
    """None si la página no trae el JSON (no se puede saber); existe=False si TikTok dice que no hay tal cuenta."""
    m = _REHIDRATACION.search(html)
    if not m:
        return None
    try:
        detalle = json.loads(m.group(1))[JSON_AMBITO].get(JSON_PERFIL)
    except (ValueError, KeyError, AttributeError):
        return None
    if not isinstance(detalle, dict):
        return None
    user = (detalle.get("userInfo") or {}).get("user") or {}
    if detalle.get("statusCode", 0) != 0 or not user.get("id"):
        return DatosPerfil(existe=False)
    return DatosPerfil(
        existe=True,
        user_id=str(user["id"]),
        usuario=user.get("uniqueId"),
        nombre=user.get("nickname") or None,
        privada=bool(user.get("privateAccount")),
        te_sigue=(user["relation"] in RELACION_TE_SIGUE) if isinstance(user.get("relation"), int) else None,
    )


# ------------------------------------------------------------------ navegación (Playwright, P3)


def abrir(ctx, usuario: str) -> InfoPerfil:
    """Abre el perfil de la persona y devuelve InfoPerfil. Lee del JSON de la página; el HTML es el plan B.
    `puede_mensaje` = hay botón «Mensaje»; si de verdad se puede escribir solo se sabe en el chat (B1)."""
    ctx.ir(url_perfil(usuario))
    datos = leer_datos(ctx.pagina.content())
    if datos is not None and not datos.existe:
        return InfoPerfil(existe=False, puede_mensaje=False)

    if not ctx.aparece(ctx.loc(sel("perfil_pagina")), ctx.carga_ms):
        ctx.revisar()
        raise ctx.error(f"No carga el perfil de @{usuario}")
    ctx.revisar()

    if datos is None:  # plan B: sin JSON, lo que se vea en la página
        titulo = ctx.loc(sel("perfil_usuario")).first.inner_text().strip().lstrip("@")
        if titulo.casefold() != usuario.strip().lstrip("@").casefold():
            raise ctx.error(f"El perfil abierto (@{titulo}) no es el de @{usuario}")
        datos = DatosPerfil(existe=True, usuario=titulo, nombre=None)

    boton = ctx.loc(sel("boton_mensaje"))
    puede = ctx.aparece(boton, 3_000) and boton.first.is_enabled()
    te_sigue = datos.te_sigue
    if te_sigue is None:
        seguir = ctx.loc(sel("boton_seguir"))
        if ctx.aparece(seguir, 2_000):
            te_sigue = te_sigue_por_boton(seguir.first.inner_text())
    return InfoPerfil(existe=True, puede_mensaje=puede, user_id=datos.user_id, nombre=datos.nombre, te_sigue=te_sigue)


def en_perfil(ctx, usuario: str) -> bool:
    """¿La página actual es el perfil de esa persona?"""
    return ctx.pagina.url.split("?")[0].rstrip("/").casefold() == url_perfil(usuario).casefold()
