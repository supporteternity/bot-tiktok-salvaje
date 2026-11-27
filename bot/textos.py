"""Textos (PLAN-OFICIAL D1, D12, tarea D1; §17 D35–D44).

- Elige una variante al azar entre las activas de un tipo, sin repetir la última usada (si hay más de una).
- Rellena {nombre} (nombre de pila; si no hay, quita el saludo con nombre limpiamente), {landing}
  (URL del panel + UTM con el ID del reel), {vendedor} (seguimiento) y {youtube} (bienvenida).
- D44: un texto público nunca lleva algo que parezca un número de teléfono.
- D43: los textos del instructivo del cliente vienen ya cargados (`sembrar`), solo en los tipos vacíos.
"""

from __future__ import annotations

import random
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bot import config
from bot.conversacion import extraer_telefono

# Públicos (comentarios) y privados (mensajes). El orden es el del panel.
TIPOS = ("dm", "respuesta", "respuesta_alt", "recordatorio", "ya_en_contacto", "confirmar_numero", "pedir_numero",
         "animo", "seguimiento", "bienvenida", "fijado")
PUBLICOS = ("respuesta", "respuesta_alt", "recordatorio", "ya_en_contacto", "fijado")
# Respuestas dentro de la conversación (D37): cuentan como acción «chat».
DE_CONVERSACION = ("confirmar_numero", "pedir_numero", "animo")
# Tipo de texto → tipo de acción en la tabla `acciones`.
ACCION_DE_TIPO = {"dm": "dm", "respuesta": "respuesta", "respuesta_alt": "respuesta_alt", "fijado": "fijar",
                  "recordatorio": "recordatorio", "ya_en_contacto": "ya_en_contacto", "confirmar_numero": "chat",
                  "pedir_numero": "chat", "animo": "chat", "seguimiento": "seguimiento", "bienvenida": "bienvenida"}

LANDING_INSTRUCTIVO = "https://academiasalvaje.com/vendedorsalvaje"
# Textos del instructivo del cliente («Instructivo del bot – Comentarios a DM», 25 nov), tal cual.
TEXTOS_INSTRUCTIVO: dict[str, tuple[str, ...]] = {
    "respuesta": (
        "Revisa tus msj bro 🔥", "Revisa tus msj salvaje 🔥", "Ya tienes la info en tus msj bro 🔥",
        "Checa tus mensajes bro 💪", "La info la tienes en tus mensajes bro! Revisa tu inbox 🔥",
        "Ve y revisa tus msj bro 🔥", "Te mandé msj 🔥", "Ya te mandé la info a tus msj 🔥", "En tus msj está la info 🔥",
    ),
    "respuesta_alt": (
        "Tírame msj bro 🔥", "Mándame msj bro 💪", "Escríbeme por inbox", "Salvaje, mándame mensaje para darte info 🔥",
    ),
    "recordatorio": ("¿Revisaste tus mensajes? 🔥", "Tienes que revisar tus msj bro 🔥"),
    "ya_en_contacto": ("Ya estamos en contacto bro 🔥",),
    "dm": (
        "Que rollo salvaje, ya miré tu comentario en mi video. Aquí te dejo la info; cuando termines de verlo me "
        "dejas tu número para que mi equipo se contacte en chinga contigo: {landing}",
    ),
    "confirmar_numero": ("Ya tenemos tu número salvaje, en un minuto mi equipo se comunica por WhatsApp.",),
    "pedir_numero": ("Perfecto, entonces déjame tu número para que alguien de mi equipo pueda darte una llamada.",),
    "animo": (
        "Salvaje, claro que sí mi bro. Aquí vas a poder cambiar tu vida. mi equipo está al pendiente de ti, mi "
        "hermano. Échale ganas, ¡vamos a ganar! 💰",
    ),
    "seguimiento": ("Que rollo bro, ¿cómo te fue?", "Que rollo bro, ¿cómo te fue con {vendedor}?"),
    "bienvenida": (
        "Gracias por seguir!! Si lo que buscas es generar en línea, mira este video primero {youtube}. Me avisas "
        "después que lo veas y mandas tu número para el siguiente paso a ganar!!",
    ),
}


class SinTextos(RuntimeError):
    """No hay ninguna variante activa de ese tipo (hay que escribirlas en el panel)."""


class SinLanding(RuntimeError):
    """El texto lleva {landing} (o {youtube}) pero no hay enlace configurado."""


class NumeroEnPublico(RuntimeError):
    """D44: un texto público parece llevar un número de teléfono."""


# ---------------------------------------------------------------- nombre


def nombre_de_pila(visible: str | None) -> str:
    """Primer nombre "humano" del nombre visible: 'Juan Pérez' → 'Juan'. Vacío si no parece un nombre."""
    if not visible:
        return ""
    for trozo in re.split(r"[\s_.\-|]+", visible.strip()):
        letras = re.sub(r"[^\w]", "", trozo)
        if letras and letras.isalpha() and len(letras) >= 2:
            return letras[:1].upper() + letras[1:].lower()
        if letras:
            return ""  # primer trozo con números u otros símbolos: parece un @usuario, mejor sin nombre
    return ""


def _sin_nombre(plantilla: str) -> str:
    # "¡{nombre}! Te dejo..." → "Te dejo..."; en medio de una frase deja un punto: "...comentar ¡{nombre}! Mira" → "...comentar. Mira"
    texto = re.sub(r"^\s*¡\s*\{nombre\}\s*!\s*", "{nombre}, ", plantilla)
    texto = re.sub(r"\s*¡\s*\{nombre\}\s*!\s*", ". ", texto)
    texto = re.sub(r"^\s*\{nombre\}\s*[,:]?\s*", "", texto)  # "{nombre}, te escribo" → "te escribo"
    if texto != plantilla:
        texto = texto[:1].upper() + texto[1:]
    texto = re.sub(r"[ \t]+\{nombre\}", "", texto)  # "Hola {nombre}," → "Hola,"
    texto = texto.replace("{nombre}", "")
    return re.sub(r"[ \t]{2,}", " ", texto).strip()


def _sin_vendedor(plantilla: str) -> str:
    """«¿cómo te fue con {vendedor}?» → «¿cómo te fue?» cuando el equipo no puso el nombre."""
    texto = re.sub(r"\s+(con|y)\s+\{vendedor\}", "", plantilla)
    return re.sub(r"[ \t]{2,}", " ", texto.replace("{vendedor}", "")).strip()


def rellenar(plantilla: str, nombre: str | None, landing: str | None, vendedor: str | None = None,
             youtube: str | None = None) -> str:
    texto = plantilla.replace("{nombre}", nombre) if nombre else _sin_nombre(plantilla)
    texto = texto.replace("{vendedor}", vendedor.strip()) if vendedor and vendedor.strip() else _sin_vendedor(texto)
    if "{landing}" in texto:
        if not landing:
            raise SinLanding("El texto lleva {landing} pero no hay landing configurada en Ajustes.")
        texto = texto.replace("{landing}", landing)
    if "{youtube}" in texto:
        if not youtube:
            raise SinLanding("El mensaje de bienvenida lleva {youtube} pero no hay enlace de YouTube en Ajustes.")
        texto = texto.replace("{youtube}", youtube)
    return texto


def numero_en_publico(tipo: str, texto: str) -> bool:
    """D44: ¿es un texto público que parece llevar un teléfono?"""
    return tipo in PUBLICOS and extraer_telefono(texto) is not None


# ---------------------------------------------------------------- landing


def landing_con_utm(url: str, utm: dict[str, str], reel_id: str, medio: str) -> str:
    """Añade los UTM (D12) a la URL. `utm_content` = ID del reel; `utm_medium` = medio (dm | comentario)."""
    partes = urlsplit(url)
    query = dict(parse_qsl(partes.query, keep_blank_values=True))
    query.update(utm)
    query["utm_medium"] = medio
    query["utm_content"] = reel_id
    return urlunsplit(partes._replace(query=urlencode(query)))


# ---------------------------------------------------------------- variantes


def elegir(conn: sqlite3.Connection, tipo: str, rng: random.Random) -> sqlite3.Row:
    activos = conn.execute("SELECT * FROM textos WHERE tipo = ? AND activo = 1 ORDER BY id", (tipo,)).fetchall()
    if not activos:
        raise SinTextos(f"No hay textos activos de tipo '{tipo}'.")
    ultima = conn.execute(
        "SELECT a.texto_id FROM acciones a JOIN textos t ON t.id = a.texto_id WHERE t.tipo = ? ORDER BY a.id DESC LIMIT 1",
        (tipo,),
    ).fetchone()
    if ultima and len(activos) > 1:
        activos = [t for t in activos if t["id"] != ultima[0]]
    return rng.choice(activos)


def preparar(
    conn: sqlite3.Connection, tipo: str, nombre_visible: str | None, reel_id: str | None,
    rng: random.Random | None = None, vendedor: str | None = None,
) -> tuple[int, str]:
    """Elige variante y la rellena. Devuelve (texto_id, texto final)."""
    rng = rng or random.Random()
    variante = elegir(conn, tipo, rng)
    cfg = config.leer_todo(conn)
    landing = None
    if cfg["landing"]:
        medio = "comentario" if tipo in PUBLICOS else "dm"
        landing = landing_con_utm(cfg["landing"], cfg["utm"], reel_id or "chat", medio=medio)
    texto = rellenar(variante["contenido"], nombre_de_pila(nombre_visible), landing, vendedor=vendedor,
                     youtube=cfg["enlace_youtube"] or None)
    if numero_en_publico(tipo, texto):
        raise NumeroEnPublico(f"Un texto público ({tipo}) parece llevar un número de teléfono: no se publica.")
    return variante["id"], texto


def marcar_usado(conn: sqlite3.Connection, texto_id: int) -> None:
    with conn:
        conn.execute("UPDATE textos SET veces_usado = veces_usado + 1 WHERE id = ?", (texto_id,))


# ---------------------------------------------------------------- textos del instructivo


def sembrar(conn: sqlite3.Connection) -> int:
    """D43: carga los textos del instructivo en los tipos que aún no tienen ninguno, y el enlace de la academia si
    no hay enlace. Nunca pisa lo que el cliente ya escribió. Devuelve cuántos textos añadió."""
    hay = {t for (t,) in conn.execute("SELECT DISTINCT tipo FROM textos")}
    nuevos = [(tipo, contenido) for tipo, lista in TEXTOS_INSTRUCTIVO.items() if tipo not in hay for contenido in lista]
    with conn:
        conn.executemany("INSERT INTO textos (tipo, contenido) VALUES (?, ?)", nuevos)
    if not config.leer(conn, "landing"):
        config.guardar(conn, "landing", LANDING_INSTRUCTIVO)
    return len(nuevos)
