"""Conversación por mensaje privado (PLAN-OFICIAL §17, D34–D37, D41). Sin TikTok: solo decide.

- `extraer_telefono`: número de teléfono escrito por la persona (EE. UU., México y resto de América).
- `clasificar`: telefono | humano (precios, quejas, dudas del programa) | duda | interes | otro.
- `decidir`: qué hace el bot con lo nuevo que escribió la persona, según su etapa (A–D).
- `palabra_de_descripcion` / `trae_palabra`: palabra clave de cada video (D41).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Secuencias de cifras con separadores habituales: «801 555 1234», «(801) 555-1234», «+52 1 55 1234 5678».
_RE_NUMERO = re.compile(r"\+?\(?\d[\d\s().\-]{6,}\d")
_RE_URL = re.compile(r"(https?://|www\.)\S+", re.I)
MIN_CIFRAS, MAX_CIFRAS = 10, 15

PREGUNTAS = ("como", "que es", "de que", "en que", "cual", "cuando", "donde", "info", "informacion", "no entiendo",
             "no se", "duda", "explica", "funciona", "se trata")

ETAPAS = {"A": "Nuevo", "B": "Ya tiene la info", "C": "Dio su número", "D": "Ya lo contactó el equipo"}


def normalizar(texto: str | None) -> str:
    """Sin mayúsculas, tildes, emojis ni signos: «¡VENTAS 🔥!» → «ventas»."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "")
    letras = "".join(c for c in sin_tildes if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", letras)).strip()


def contiene(texto: str | None, frase: str) -> bool:
    """¿El texto trae la frase como palabras enteras? (sin mayúsculas, tildes ni emojis)"""
    f = normalizar(frase)
    return bool(f) and re.search(rf"(?<!\w){re.escape(f)}(?!\w)", normalizar(texto)) is not None


# ---------------------------------------------------------------- teléfono


def extraer_telefono(texto: str | None) -> str | None:
    """El primer número de teléfono del texto, solo cifras (con «+» delante si lo traía o si lleva prefijo de
    país). None si no hay. Los enlaces no cuentan (los IDs de video son números largos)."""
    limpio = _RE_URL.sub(" ", texto or "")
    for trozo in _RE_NUMERO.findall(limpio):
        cifras = re.sub(r"\D", "", trozo)
        if not MIN_CIFRAS <= len(cifras) <= MAX_CIFRAS or len(set(cifras)) == 1:
            continue
        if trozo.lstrip("(").startswith("+") or len(cifras) > 10:
            return "+" + cifras
        return cifras
    return None


# ---------------------------------------------------------------- clasificar


def clasificar(texto: str, palabras_humano: list[str], palabras_interes: list[str]) -> str:
    if extraer_telefono(texto):
        return "telefono"
    if any(contiene(texto, p) for p in palabras_humano if p.strip()):
        return "humano"
    limpio = normalizar(texto)
    if "?" in texto or "¿" in texto or any(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", limpio) for p in PREGUNTAS):
        return "duda"
    if any(contiene(texto, p) for p in palabras_interes if p.strip()):
        return "interes"
    return "otro"


# ---------------------------------------------------------------- decidir


@dataclass(frozen=True)
class Decision:
    respuesta: str | None = None  # tipo de texto a enviar (dm, confirmar_numero, pedir_numero, animo)
    telefono: str | None = None  # número nuevo que dio la persona
    humano: str | None = None  # motivo para pasarla a una persona (el bot deja de contestarle)
    etapa: str | None = None  # etapa nueva


def decidir(etapa: str, humano: bool, respuestas_auto: int, ya_enviados: set[str], telefono_actual: str | None,
            mensajes: list[str], cfg: dict) -> Decision:
    """Qué hacer con los mensajes NUEVOS de la persona (D37). Como mucho una respuesta por revisión."""
    if not mensajes:
        return Decision()
    clases = [clasificar(m, cfg["palabras_humano"], cfg["palabras_interes"]) for m in mensajes]

    telefono = next((extraer_telefono(m) for m in mensajes if extraer_telefono(m)), None)
    if telefono:
        if telefono == telefono_actual:
            return Decision()
        ya_tenia = etapa in ("C", "D")
        return Decision(respuesta=None if ya_tenia or humano else "confirmar_numero", telefono=telefono,
                        humano="dio su número", etapa=None if etapa == "D" else "C")

    if humano or etapa in ("C", "D"):
        return Decision()  # ya la atiende el equipo

    if "humano" in clases:
        texto = mensajes[clases.index("humano")]
        return Decision(humano=f"pregunta algo que el bot no contesta: «{texto[:200]}»")

    if etapa == "A":  # escribió sin haber recibido la info (p. ej. tras «Tírame msj»): se le manda el enlace
        return Decision(respuesta="dm", etapa="B")

    if respuestas_auto >= cfg["max_respuestas_auto"]:
        return Decision(humano="sigue escribiendo sin dejar su número")

    if "interes" in clases and "animo" not in ya_enviados:
        return Decision(respuesta="animo")
    if "pedir_numero" not in ya_enviados:
        return Decision(respuesta="pedir_numero")
    if "duda" in clases:
        return Decision(humano=f"sigue con dudas: «{mensajes[clases.index('duda')][:200]}»")
    return Decision()


# ---------------------------------------------------------------- palabra clave (D41)

_RE_PALABRA = re.compile(r"coment[ae]\w*\s+(?:la\s+palabra\s+)?[\"“«'‘]?\s*([^\s\"”»'’.,;:!¡?¿]+)", re.I)


def palabra_de_descripcion(descripcion: str | None) -> str | None:
    """«Comenta "VENTAS" y te muestro cómo» → «VENTAS». None si el texto del video no la trae."""
    m = _RE_PALABRA.search(descripcion or "")
    return m.group(1).strip() if m else None


def trae_palabra(comentario: str | None, palabra: str | None) -> bool:
    return bool(palabra) and contiene(comentario, palabra)
