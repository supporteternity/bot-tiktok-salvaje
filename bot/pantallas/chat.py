"""Chat (PLAN-OFICIAL §9 P4, tarea D2): abrir el chat desde el perfil, escribir a ritmo humano, enviar y
VERIFICAR que el mensaje aparece como nuestro. En modo de prueba se escribe y se borra: nunca se envía.

Cómo se sabe que un mensaje es nuestro (visto en fixtures reales): el avatar de cada mensaje enlaza al
perfil de quien lo envió (`a[href="/@cuenta"]`).
"""

from __future__ import annotations

import logging
import re

from bot.pantallas import AvisoTikTok, ErrorPantalla, ResultadoEnvio
from bot.pantallas import perfil as pantalla_perfil
from bot.pantallas.comun import Contexto, escrito_bien, normalizar, teclear, vaciar
from bot.selectores import sel, url_perfil

log = logging.getLogger("bot.pantallas.chat")

PREFIJO_VERIFICAR = 30  # caracteres del principio del texto que tienen que verse en el mensaje enviado
# Un enlace en un mensaje: «https://…», «www.…» o un dominio suelto («academia.com/clase»).
_ENLACE = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|net|org|io|co|mx|us|app|link|ly|me|info|site|online|page)\b", re.I)


# Rechazo de TikTok al enviar un privado (visto el 26 nov grabando la respuesta del servidor): status_code 7195,
# «no se envió para proteger nuestra comunidad», escena «sending_ban». No depende del idioma de la página.
CODIGO_RECHAZO = 7195
_RECHAZO = re.compile(rf'"status_code"\s*:\s*{CODIGO_RECHAZO}\b|sending_ban')


class _OidoRechazo:
    """Mientras se envía, guarda las respuestas JSON que llegan; `rechazado()` dice si alguna es el 7195.
    Solo escucha: no cambia nada de lo que se envía."""

    def __init__(self, pagina):
        self.pagina, self.respuestas = pagina, []

    def _al_responder(self, respuesta) -> None:
        try:
            if "json" in (respuesta.headers.get("content-type") or ""):
                self.respuestas.append(respuesta)
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        self.pagina.on("response", self._al_responder)
        return self

    def __exit__(self, *_exc) -> None:
        self.pagina.remove_listener("response", self._al_responder)

    def rechazado(self) -> bool:
        for respuesta in self.respuestas:
            try:
                if _RECHAZO.search(respuesta.text()):
                    return True
            except Exception:  # noqa: BLE001 — respuesta ya descartada por el navegador
                continue
        return False


def _con_enlace(propios) -> bool:
    """¿Alguno de nuestros mensajes del chat lleva un enlace? (D36 afinada, 26 nov)."""
    textos = propios.locator(sel("chat_mensaje_texto").css).all_inner_texts()
    return any(_ENLACE.search(t) for t in textos)


def abrir(ctx: Contexto, usuario: str, solo_mirar: bool = False) -> bool:
    """Abre el chat con la persona pulsando «Mensaje» en su perfil.
    True = se puede escribir (hay editor); False = mensajes cerrados. ErrorPantalla si no carga.
    `solo_mirar`: para comprobar un envío (no se escribe nada), con la cabecera vacía basta con que la página
    traiga el perfil de esa persona aunque el chat ya tenga mensajes (los nuestros, recién enviados)."""
    usuario = usuario.strip().lstrip("@")
    if not pantalla_perfil.en_perfil(ctx, usuario):
        ctx.ir(url_perfil(usuario))

    boton = ctx.loc(sel("boton_mensaje"))
    if not ctx.aparece(boton):
        ctx.revisar()
        raise ctx.error(f"No aparece el botón «Mensaje» en el perfil de @{usuario}")
    boton.first.click()
    ctx.revisar()

    cabecera = ctx.loc(sel("chat_usuario")).filter(has_text=re.compile(rf"^\s*@{re.escape(usuario)}\s*$", re.I))
    # Conversación nueva (nunca habíais hablado): TikTok deja la cabecera vacía («@», visto el 25 nov).
    vacia = ctx.loc(sel("chat_usuario")).filter(has_text=re.compile(r"^\s*@\s*$"))
    if not ctx.aparece(cabecera.or_(vacia), ctx.carga_ms):
        ctx.revisar()
        raise ctx.error(f"No se abrió el chat con @{usuario}")
    if not cabecera.count():
        # Puede ser que aún esté cargando: se da un margen a que salga el nombre.
        llena = ctx.loc(sel("chat_usuario")).filter(has_text=re.compile(r"@\S"))
        if ctx.aparece(llena, 3_000) and not cabecera.count():
            ctx.revisar()
            raise ctx.error(f"Se abrió el chat de otra persona en lugar del de @{usuario}")
        if not cabecera.count() and not _chat_nuevo_con(ctx, usuario, solo_mirar):
            ctx.revisar()
            raise ctx.error(f"No se pudo confirmar que el chat abierto es el de @{usuario}")

    editor, cerrado = ctx.loc(sel("chat_editor")), ctx.loc(sel("chat_cerrado"))
    if not ctx.aparece(editor.or_(cerrado)):
        ctx.revisar()
        raise ctx.error(f"El chat con @{usuario} no muestra ni el cuadro de texto ni el aviso de cerrado")
    ctx.revisar()
    return editor.count() > 0 and editor.first.is_visible()


def _chat_nuevo_con(ctx: Contexto, usuario: str, solo_mirar: bool = False) -> bool:
    """Cabecera vacía: es el chat correcto si estamos en Mensajes, la página aún trae el perfil de esa persona
    (se llegó pulsando «Mensaje» en su perfil) y el chat está vacío. Una conversación ya existente (de otra
    persona) tendría mensajes y nombre en la cabecera: entonces NO se escribe."""
    if "/messages" not in ctx.pagina.url:
        return False
    datos = pantalla_perfil.leer_datos(ctx.pagina.content())
    if not (datos and datos.existe and (datos.usuario or "").casefold() == usuario.casefold()):
        return False
    if not ctx.loc(sel("chat_mensaje")).count() or solo_mirar:
        return True
    # Ya hay mensajes y la cabecera sigue vacía (visto el 27 nov con @persona.dos: un «Hola» nuestro del día antes).
    # Se escribe solo si la conversación seleccionada en la lista es la de esa persona (su id va en data-conv-id).
    return _conversacion_abierta_es_de(ctx, datos.user_id)


def _conversacion_abierta_es_de(ctx: Contexto, user_id: str | None) -> bool:
    if not user_id:
        return False
    abierta = ctx.loc(sel("chat_conversacion_abierta"))
    if abierta.count() != 1:
        return False
    conv = abierta.first.get_attribute("data-conv-id") or ""
    return user_id in conv.split(":")


def _propios(ctx: Contexto):
    return ctx.loc(sel("chat_mensaje_propio"), cuenta=ctx.cuenta)


def enviar(ctx: Contexto, usuario: str, texto: str, simulado: bool, solo_si_nuevo: bool = False) -> ResultadoEnvio:
    """`solo_si_nuevo` (D36): si en el chat ya hay un mensaje nuestro CON ENLACE (ya tiene la info), no se escribe
    nada → «ya_hablado». Un saludo nuestro sin enlace (p. ej. alguien del equipo escribió «hola» a mano) no cuenta:
    se le manda el enlace igual (visto en la prueba real del 26 nov)."""
    try:
        return _enviar(ctx, usuario, texto, simulado, solo_si_nuevo)
    except (ErrorPantalla, AvisoTikTok) as e:
        if not getattr(e, "tras_enviar", False):
            e.sin_enviar = True  # falló antes de pulsar enviar: seguro que no salió nada (se puede reintentar)
        raise
    except Exception as e:  # noqa: BLE001 — Playwright (clic tapado, tiempo agotado…) antes de pulsar enviar
        err = ErrorPantalla(f"Fallo antes de enviar a @{usuario}: {e}")
        err.sin_enviar = True
        raise err from e


def _enviar(ctx: Contexto, usuario: str, texto: str, simulado: bool, solo_si_nuevo: bool = False) -> ResultadoEnvio:
    if not abrir(ctx, usuario):
        return "cerrado"

    propios = _propios(ctx)
    if solo_si_nuevo:
        ctx.aparece(ctx.loc(sel("chat_mensaje")), 3_000)  # la lista de mensajes puede tardar un poco en salir
        if propios.count() and _con_enlace(propios):
            log.info("Ya le mandamos un enlace a @%s: no se le manda otra vez", usuario)
            return "ya_hablado"
        if propios.count():
            log.info("Hay mensajes nuestros con @%s pero ninguno con enlace: se le manda el enlace", usuario)
    antes = propios.count()
    editor = ctx.loc(sel("chat_editor")).first
    if normalizar(editor.inner_text()):
        vaciar(ctx, editor)  # borrador de otra vez: fuera
    teclear(ctx, editor, texto)
    ctx.revisar()
    if not escrito_bien(editor, texto):
        vaciar(ctx, editor)
        raise ctx.error("El texto escrito en el chat no coincide con el que había que escribir")

    if simulado:  # modo de prueba: todo menos pulsar enviar
        vaciar(ctx, editor)
        ctx.revisar()
        return "enviado"

    boton = ctx.loc(sel("chat_boton_enviar"))
    oido = _OidoRechazo(ctx.pagina)
    try:
        with oido:
            try:
                if boton.count() and boton.first.is_visible():
                    boton.first.click()
                else:
                    ctx.pagina.keyboard.press("Enter")
            except Exception as e:  # noqa: BLE001 — pudo salir: no se puede asegurar que no se envió
                err = ErrorPantalla(f"Fallo al pulsar enviar a @{usuario}: {e}")
                err.tras_enviar = True
                raise err from e
            resultado = _verificar(ctx, usuario, texto, propios, antes)
            if resultado == "enviado":
                # El momento que se deja a TikTok para guardarlo, con la escucha abierta: su respuesta llega aquí.
                ctx.pagina.wait_for_timeout(ctx.asentar_ms)
        if oido.rechazado():
            log.warning("TikTok no dejó pasar el mensaje a @%s (código %s: «para proteger nuestra comunidad»)",
                        usuario, CODIGO_RECHAZO)
            return "bloqueado"
        if resultado != "enviado":
            return resultado
        guardado = _guardado(ctx, usuario, texto)
    except (ErrorPantalla, AvisoTikTok) as e:
        e.tras_enviar = True
        raise
    except Exception as e:  # noqa: BLE001 — ya se pulsó enviar: no se sabe si salió
        err = ErrorPantalla(f"Fallo al comprobar el envío a @{usuario}: {e}")
        err.tras_enviar = True
        raise err from e
    if not guardado:
        # Salió en pantalla pero al reabrir el chat no se ve: TikTok no lo guardó (visto el 26 nov: lo rechaza con
        # «no se envió para proteger nuestra comunidad» a algunos desconocidos). Nunca se reenvía (D46); el
        # ejecutor le contesta en público para que escriba (D47).
        log.warning("El mensaje a @%s salió en pantalla pero TikTok no lo guardó: se le invitará en público a escribir",
                    usuario)
        return "dudoso"
    return "enviado"


def _guardado(ctx: Contexto, usuario: str, texto: str) -> bool:
    """Casos reales del 25–26 nov: TikTok enseña el mensaje nada más enviarlo, pero a veces no lo guarda y
    no le llega a la persona. Para saberlo se le da un momento, se vuelve a abrir el chat desde cero y se
    busca el mensaje entre los nuestros (la espera la hace `_enviar`). False = no se vio (no prueba que no llegara: el chat puede ir lento)."""
    ctx.revisar()  # el momento para que TikTok lo guarde ya pasó en `_enviar` (escuchando su respuesta)
    ctx.ir(url_perfil(usuario))  # recarga de verdad: lo que salga ahora viene de TikTok, no de la pantalla
    abrir(ctx, usuario, solo_mirar=True)
    propios = _propios(ctx)
    if not ctx.aparece(propios, ctx.verificar_ms):
        return False
    buscado = normalizar(texto)[:PREFIJO_VERIFICAR]
    for i in range(propios.count() - 1, -1, -1):
        caja = propios.nth(i).locator(sel("chat_mensaje_texto").css)
        if caja.count() and normalizar(caja.first.inner_text()).startswith(buscado):
            return True
    return False


def _verificar(ctx: Contexto, usuario: str, texto: str, propios, antes: int) -> ResultadoEnvio:

    nuevo = propios.nth(antes)
    if not ctx.aparece(nuevo, ctx.verificar_ms):
        ctx.revisar()  # si hay un aviso, que mande el aviso (el comentario queda en dm_enviando)
        log.warning("Enviado a @%s sin confirmación visible", usuario)
        return "dudoso"
    ctx.revisar()

    texto_visto = normalizar(nuevo.locator(sel("chat_mensaje_texto").css).first.inner_text())
    if not texto_visto.startswith(normalizar(texto)[:PREFIJO_VERIFICAR]):
        log.warning("El último mensaje propio a @%s no coincide con el enviado", usuario)
        return "dudoso"

    aviso = nuevo.locator(sel("chat_aviso_mensaje").css)
    if aviso.count() and normalizar(aviso.first.inner_text()):
        log.info("Mensaje a @%s no entregado: %s", usuario, normalizar(aviso.first.inner_text()))
        return "cerrado"
    return "enviado"


def ya_enviado(ctx: Contexto, usuario: str) -> bool | None:
    """Tras un corte en `dm_enviando`: ¿hay ya un mensaje nuestro en el chat? None = no se puede comprobar.
    (False y None acaban igual en el ejecutor, en `dudoso`: nunca se reenvía a ciegas.)"""
    try:
        if not abrir(ctx, usuario):
            return None
    except AvisoTikTok:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo abrir el chat con @%s para comprobar: %s", usuario, e)
        return None
    if ctx.aparece(_propios(ctx), ctx.verificar_ms):
        return True
    return None if ctx.loc(sel("chat_mensaje")).count() == 0 else False
