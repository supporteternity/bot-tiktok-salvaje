"""Piezas comunes de las pantallas reales (Playwright): contexto, tecleo humano, escucha de JSON y errores."""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Locator, Page, Response

from bot.pantallas import ErrorPantalla
from bot.selectores import Selector

log = logging.getLogger("bot.pantallas")


@dataclass
class Contexto:
    """Lo que necesita cada pantalla. `revisar` = vigía (se llama tras CADA paso; lanza AvisoTikTok)."""

    nav: Any  # bot.navegador.Navegador (o uno de prueba con .pagina y .captura)
    cuenta: str  # cuenta propia, sin @
    revisar: Callable[[], None]
    tecleo_ms: tuple[int, int] = (60, 160)
    rng: random.Random = field(default_factory=random.Random)
    # Esperas máximas (ms). Son esperas POR CONDICIÓN: se sigue en cuanto aparece lo esperado.
    carga_ms: int = 20_000
    elemento_ms: int = 10_000
    verificar_ms: int = 15_000
    # Tras enviar, cuánto se deja a TikTok para guardar el mensaje antes de reabrir el chat y comprobarlo.
    asentar_ms: int = 5_000

    @property
    def pagina(self) -> Page:
        return self.nav.pagina

    def loc(self, s: Selector, **valores: str) -> Locator:
        return self.pagina.locator(s.con(**valores) if valores else s.css)

    def ir(self, url: str) -> None:
        self.pagina.goto(url, wait_until="domcontentloaded", timeout=self.carga_ms)
        self.revisar()

    def error(self, detalle: str) -> ErrorPantalla:
        """ErrorPantalla con captura (PNG + HTML) para revisar a distancia. Se usa con `raise ctx.error(...)`."""
        captura = None
        try:
            png, _ = self.nav.captura("error-" + detalle[:40])
            captura = str(png)
        except Exception as e:  # noqa: BLE001 — sin captura también hay que avisar
            log.warning("No se pudo guardar la captura: %s", e)
        return ErrorPantalla(detalle, captura)

    def aparece(self, loc: Locator, ms: int | None = None) -> bool:
        try:
            loc.first.wait_for(state="visible", timeout=self.elemento_ms if ms is None else ms)
            return True
        except Exception:  # noqa: BLE001 — TimeoutError de Playwright
            return False


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def teclear(ctx: Contexto, campo: Locator, texto: str) -> None:
    """Escribe con el teclado (no con el portapapeles), carácter a carácter a ritmo humano.
    Los saltos de línea van con Mayús+Intro (Intro solo enviaría)."""
    campo.click()
    pagina = ctx.pagina
    for i, linea in enumerate(texto.split("\n")):
        if i:
            pagina.keyboard.press("Shift+Enter")
        for caracter in linea:
            pagina.keyboard.type(caracter)
            pagina.wait_for_timeout(ctx.rng.uniform(*ctx.tecleo_ms))


def vaciar(ctx: Contexto, campo: Locator) -> None:
    campo.click()
    ctx.pagina.keyboard.press("ControlOrMeta+a")
    ctx.pagina.keyboard.press("Backspace")


def escrito_bien(campo: Locator, texto: str) -> bool:
    return normalizar(campo.inner_text()) == normalizar(texto)


class Escucha:
    """Guarda las respuestas de la página cuya URL contiene alguno de los trozos (page.on("response")).
    El JSON se lee después, fuera del evento (Playwright no deja leer el cuerpo dentro del aviso)."""

    def __init__(self, pagina: Page, *trozos: str):
        self.pagina, self.trozos = pagina, trozos
        self.respuestas: list[Response] = []
        self._leidas = 0

    def _al_responder(self, respuesta: Response) -> None:
        if any(t in respuesta.url for t in self.trozos):
            self.respuestas.append(respuesta)

    def __enter__(self) -> Escucha:
        self.pagina.on("response", self._al_responder)
        return self

    def __exit__(self, *_exc) -> None:
        self.pagina.remove_listener("response", self._al_responder)

    def nuevos_json(self) -> list[dict]:
        """JSON de las respuestas que aún no se habían leído (las que no son JSON se ignoran)."""
        datos = []
        for respuesta in self.respuestas[self._leidas:]:
            try:
                cuerpo = respuesta.json()
                if isinstance(cuerpo, dict):
                    datos.append(cuerpo)
            except Exception:  # noqa: BLE001
                log.debug("Respuesta sin JSON: %s", respuesta.url)
        self._leidas = len(self.respuestas)
        return datos
