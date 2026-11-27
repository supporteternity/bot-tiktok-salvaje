"""Vigía (PLAN-OFICIAL §9 P5/P6, tarea B2): tras CADA paso en el navegador se revisa la pantalla.
Si hay algo que no sea normal, se lanza AvisoTikTok y el bucle frena. Ante la duda, parar.

Señales (de más a menos fiables):
- Sesión: el JSON que trae la propia página (`webapp.app-context.user`) dice qué cuenta está dentro.
  Sin `user`, con botón/ventana de inicio de sesión o en /login → sesion_cerrada. Otra cuenta → también para.
- Captcha: elementos con "captcha" en id/clase/data-e2e o iframes de verificación.
- Avisos (demasiado rápido, restringido, verificación): textos dentro de ZONAS DE AVISO (role alert/status/
  dialog, aria-live, clases toast/modal/notice…). Nunca en el resto de la página, donde están los
  comentarios de la gente («vas demasiado rápido» en un comentario no es un aviso), ni en scripts.
- Ventana modal (aria-modal / alertdialog) que no está en la lista de conocidas → desconocido.

Los textos de P5 están sacados de avisos conocidos de TikTok; se confirman con capturas reales
cuando aparezcan (cada aviso real se guarda con captura y HTML).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from html.parser import HTMLParser

from bot.pantallas import AvisoTikTok
from bot.selectores import AVISOS, DIALOGOS_CONOCIDOS, JSON_AMBITO, JSON_SESION, MARCAS_SIN_SESION
from bot.selectores import RE_JSON_REHIDRATACION as _REHIDRATACION

_SCRIPT = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.S | re.I)
_ETIQUETA = re.compile(r"<[^>]+>")


def _marcado(html: str) -> str:
    """HTML sin scripts ni estilos (solo lo que forma la página)."""
    return _SCRIPT.sub(" ", html)


def texto_visible(html: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_ETIQUETA.sub(" ", _marcado(html)))).casefold()


_VACIAS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_CLASES_AVISO = re.compile(r"toast|snackbar|notice|alert|modal|tooltip|banner|popup", re.I)


def _es_zona_aviso(atributos: dict[str, str]) -> bool:
    if (atributos.get("aria-label") or "").casefold() in DIALOGOS_CONOCIDOS:
        return False  # el panel de notificaciones enseña comentarios de la gente: no es un aviso
    rol = (atributos.get("role") or "").lower()
    if rol == "log":
        return False  # lista de mensajes del chat (aria-live): son mensajes de la gente o nuestros, no avisos
    return (rol in ("alert", "alertdialog", "status", "dialog") or "aria-live" in atributos
            or atributos.get("aria-modal") == "true" or bool(_CLASES_AVISO.search(atributos.get("class") or "")))


class _TextoDeAvisos(HTMLParser):
    """Junta el texto que está dentro de zonas de aviso (anidadas en cualquier nivel)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pila: list[bool] = []  # por cada etiqueta abierta: ¿abre una zona de aviso?
        self.zonas = 0
        self.ignorar = 0
        self.trozos: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _VACIAS:
            return
        if tag in ("script", "style", "noscript", "template"):
            self.ignorar += 1
        zona = _es_zona_aviso({k: v or "" for k, v in attrs})
        self.pila.append(zona)
        self.zonas += zona

    def handle_endtag(self, tag):
        if tag in _VACIAS or not self.pila:
            return
        if tag in ("script", "style", "noscript", "template"):
            self.ignorar = max(0, self.ignorar - 1)
        self.zonas -= self.pila.pop()

    def handle_data(self, data):
        if self.zonas > 0 and not self.ignorar:
            self.trozos.append(data)


def texto_de_avisos(html: str) -> str:
    lector = _TextoDeAvisos()
    lector.feed(html)
    return re.sub(r"\s+", " ", " ".join(lector.trozos)).casefold()


def usuario_en_sesion(html: str) -> str | None:
    """@usuario de la cuenta con la sesión iniciada, según el JSON de la página. None si no hay sesión
    (o si la página no trae ese JSON; ver `hay_dato_de_sesion`)."""
    m = _REHIDRATACION.search(html)
    if not m:
        return None
    try:
        app = json.loads(m.group(1))[JSON_AMBITO].get(JSON_SESION, {})
    except (ValueError, KeyError, AttributeError):
        return None
    user = app.get("user")
    return user.get("uniqueId") if isinstance(user, dict) else None


def analizar(html: str, url: str, cuenta: str = "") -> tuple[str, str] | None:
    """(clase, detalle) si hay que parar; None si la pantalla es normal."""
    if "/login" in url.split("?")[0]:
        return "sesion_cerrada", f"TikTok ha llevado al bot a la página de inicio de sesión ({url})"

    marcado = _marcado(html)
    if any(m in marcado for m in MARCAS_SIN_SESION):
        return "sesion_cerrada", "La página muestra el botón o la ventana de «Iniciar sesión»"

    tiene_json = _REHIDRATACION.search(html) is not None
    usuario = usuario_en_sesion(html)
    if tiene_json and usuario is None:
        return "sesion_cerrada", "La página no tiene ninguna cuenta con sesión iniciada"
    if usuario and cuenta and usuario.casefold() != cuenta.strip().lstrip("@").casefold():
        return "sesion_cerrada", f"Hay otra cuenta iniciada (@{usuario}) en lugar de @{cuenta}"

    if re.search(r'<[^>]+(?:id|class|data-e2e)="[^"]*captcha[^"]*"', marcado, re.I) or re.search(
        r'<iframe[^>]+src="[^"]*(?:captcha|verify)[^"]*"', marcado, re.I
    ):
        return "captcha", "TikTok muestra un captcha"

    avisos = texto_de_avisos(html)
    for clase, frases in AVISOS.items():
        for frase in frases:
            if frase in avisos:
                return clase, f"Aviso de TikTok: «{frase}»"

    for m in re.finditer(r'<[^>]+(?:aria-modal="true"|role="alertdialog")[^>]*>', marcado):
        etiqueta = m.group(0)
        if re.search(r'class="[^"]*--exiting', etiqueta):
            continue  # ventana en plena animación de cierre: ya se va (caso real del 25 nov)
        # Sin nombre en la propia ventana: el primer data-e2e de dentro (TikTok lo pone en el contenido).
        nombre = (re.search(r'aria-label="([^"]*)"', etiqueta) or re.search(r'data-e2e="([^"]*)"', etiqueta)
                  or re.search(r'data-e2e="([^"]*)"', marcado[m.end():m.end() + 1500]))
        nombre = nombre.group(1) if nombre else "sin nombre"
        if nombre.casefold() not in DIALOGOS_CONOCIDOS:
            return "desconocido", f"Ventana desconocida en pantalla: «{nombre}»"
    return None


def revisar(nav, cuenta: str) -> None:
    """Revisa la página actual del navegador del bot. Lanza AvisoTikTok (con captura) si hay que parar."""
    pagina = nav.pagina
    resultado = analizar(pagina.content(), pagina.url, cuenta)
    if resultado is None:
        return
    clase, detalle = resultado
    png, _ = nav.captura(f"vigia-{clase}")
    raise AvisoTikTok(clase, detalle, str(png))
