"""PantallasTikTok: la implementación real (Playwright) del contrato `Pantallas` sobre el Navegador del bot.

- Tras cada paso se llama al vigía (`vigia.revisar`): si hay aviso, captcha, verificación o sesión
  cerrada, lanza AvisoTikTok y el bucle frena. Nunca se resuelve nada solo.
- En `modo_prueba` (simulado=True) se hace todo menos pulsar enviar/publicar.
- La cuenta propia y la velocidad de tecleo se leen de `config` en cada llamada (el panel puede cambiarlas).
"""

from __future__ import annotations

import random
import sqlite3

from playwright.sync_api import Error as ErrorPlaywright

from bot import config
from bot.pantallas import InfoPerfil, ResultadoEnvio, ResultadoFijado, ResultadoRespuesta, vigia
from bot.pantallas import bandeja, chat, perfil, reel, studio_comentarios
from bot.pantallas.comun import Contexto
from bot.selectores import URL_EN_BLANCO, URL_MENSAJES, URL_STUDIO_COMENTARIOS, sel, url_perfil


class NavegadorCerrado(BaseException):
    """La ventana de Chrome del bot se cerró. Hereda de BaseException a propósito: no es un error técnico
    más (el bucle no debe contarlo ni pausarse 30 min); el proceso termina y el servicio lo reabre."""


class PantallasTikTok:
    def __init__(self, nav, conn: sqlite3.Connection, rng: random.Random | None = None, **esperas: int):
        self.nav, self.conn = nav, conn
        self.rng = rng or random.Random()
        self.esperas = esperas  # carga_ms / elemento_ms / verificar_ms (los tests las acortan)

    def comprobar_navegador(self) -> None:
        """Cada vuelta del bucle (también en pausa o frenado): si cerraron Chrome, NavegadorCerrado.
        Se le pregunta de verdad al navegador: mientras el bot duerme, Playwright no se entera del cierre y
        `is_closed()` seguiría diciendo que está abierta."""
        self._vivo()
        try:
            self.nav.pagina.evaluate("1")
        except ErrorPlaywright:
            if self.nav.pagina.is_closed():
                raise NavegadorCerrado() from None
            raise

    def _vivo(self) -> None:
        pagina = getattr(self.nav, "pagina", None)
        if pagina is None or pagina.is_closed():
            raise NavegadorCerrado()

    def contexto(self) -> Contexto:
        self._vivo()
        cuenta = (config.leer(self.conn, "cuenta") or "").strip().lstrip("@")
        tecleo = tuple(config.leer(self.conn, "velocidad_tecleo_ms"))
        return Contexto(nav=self.nav, cuenta=cuenta, revisar=lambda: vigia.revisar(self.nav, cuenta),
                        tecleo_ms=tecleo, rng=self.rng, **self.esperas)

    # ------------------------------------------------------------ contrato Pantallas

    def abrir_perfil(self, usuario: str) -> InfoPerfil:
        return perfil.abrir(self.contexto(), usuario)

    def enviar_dm(self, usuario: str, texto: str, simulado: bool, solo_si_nuevo: bool = False) -> ResultadoEnvio:
        return chat.enviar(self.contexto(), usuario, texto, simulado, solo_si_nuevo)

    def leer_bandeja(self):
        return bandeja.leer_lista(self.contexto())

    def leer_chat(self, chat_en_lista, aceptar: bool):
        return bandeja.leer_chat(self.contexto(), chat_en_lista, aceptar)

    def seguidores_nuevos(self) -> list[str]:
        return bandeja.seguidores_nuevos(self.contexto())

    def dm_ya_enviado(self, usuario: str) -> bool | None:
        return chat.ya_enviado(self.contexto(), usuario)

    def responder(self, reel_id: str, comentario_id: str, texto: str, simulado: bool) -> ResultadoRespuesta:
        fila = self.conn.execute(
            "SELECT k.usuario, c.texto FROM comentarios c JOIN contactos k ON k.user_id = c.user_id WHERE c.id = ?",
            (comentario_id,),
        ).fetchone()
        if fila is None:
            return "no_disponible"
        # Desde TikTok Studio (25 nov): abrir los comentarios en la página del vídeo hace salir un captcha.
        return studio_comentarios.responder(self.contexto(), comentario_id, fila["usuario"], fila["texto"] or "",
                                            texto, simulado)

    def publicar_fijado(self, reel_id: str, texto: str, simulado: bool) -> ResultadoFijado:
        return reel.publicar_fijado(self.contexto(), reel_id, texto, simulado)

    def en_blanco(self) -> None:
        self._vivo()
        self.nav.pagina.goto(URL_EN_BLANCO)

    def cuenta_en_sesion(self) -> str | None:
        """@ de la cuenta con la sesión iniciada en el Chrome del bot (None = no hay sesión). Solo mira.
        Captcha y avisos siguen lanzando AvisoTikTok; «sin sesión» no, porque es justo lo que se pregunta."""
        self._vivo()
        pagina = self.nav.pagina
        pagina.goto(URL_MENSAJES, wait_until="domcontentloaded", timeout=self.esperas.get("carga_ms", 20_000))
        pagina.wait_for_timeout(2_000)
        html = pagina.content()
        aviso = vigia.analizar(html, pagina.url, "")
        if aviso is not None and aviso[0] != "sesion_cerrada":
            vigia.revisar(self.nav, "")
        usuario = vigia.usuario_en_sesion(html)
        self.captura_sesion = None
        if usuario is None:
            try:
                png, _ = self.nav.captura("sin-sesion")
                self.captura_sesion = str(png)
            except Exception:  # noqa: BLE001 — sin captura también se avisa
                pass
        return usuario

    def autoprueba(self) -> tuple[bool, str]:
        """Abre perfil propio, mensajes y el reel más reciente SIN actuar y comprueba los selectores."""
        ctx = self.contexto()
        if not ctx.cuenta:
            return False, "falta la cuenta propia en Ajustes"
        fallos: list[str] = []

        def mirar(url: str, nombres: tuple[str, ...]) -> None:
            ctx.ir(url)
            for nombre in nombres:
                if not ctx.aparece(ctx.loc(sel(nombre)), ctx.elemento_ms):
                    fallos.append(f"{nombre} ({url})")

        mirar(url_perfil(ctx.cuenta), ("perfil_pagina", "icono_perfil_propio"))
        mirar(URL_MENSAJES, ("icono_mensajes", "chat_conversacion"))
        # Studio y no la página del vídeo: abrir los comentarios del vídeo hace salir un captcha (25 nov).
        if self.conn.execute("SELECT 1 FROM reels WHERE activo = 1 LIMIT 1").fetchone():
            mirar(URL_STUDIO_COMENTARIOS, ("studio_lista_comentarios", "studio_comentario"))
        self.en_blanco()
        return (not fallos), ("todo encontrado" if not fallos else "no se encontró: " + ", ".join(fallos))
