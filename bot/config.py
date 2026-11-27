"""Configuración (PLAN-OFICIAL §7.1) y secretos.

- Los valores viven en la tabla `config` de bot.db (clave → JSON). El panel los edita y el bot los
  lee en cada vuelta.
- `config.toml` (carpeta de datos) solo da valores INICIALES: se aplica a las claves que aún no
  existen en la base; después manda lo que se cambie desde el panel.
- Los secretos (SMTP, contraseña del panel) solo están en `.env` (carpeta de datos). Nunca se
  guardan en la base ni salen en los logs.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from platformdirs import user_data_dir

NOMBRE_APP = "BotTikTok"
# Permite usar otra carpeta de datos (tests, pruebas). Nunca apunta dentro del código.
VAR_CARPETA_DATOS = "BOT_TIKTOK_DATOS"
ZONA_HORARIA = "America/Denver"  # Utah

MODOS_PUBLICA = ("solo_cerrados", "todos", "solo_publicas")

DEFAULTS: dict[str, Any] = {
    "cuenta": "",  # cuenta propia (sin @); sus comentarios se saltan
    "nivel_ritmo": 3,  # D31: empieza en «cada ~2,5 min»; el cliente la cambia en el panel
    "intervalo_variacion": 0.5,  # ±50 %
    "pausa_min_seg": 20,
    "descanso_cada": [12, 20],  # acciones
    "descanso_duracion_min": [10, 30],
    "descanso_diario_horas": 4,
    "descanso_diario_inicio": ["02:00", "04:00"],  # hora de Utah, al azar en el rango
    "tope_dm_dia": 100,  # máximo de mensajes privados al día (ver ritmo.TOPES_DM_DIA)
    "tope_dm_seguidores_dia": 100,  # mensajes extra al día a quien sigue la cuenta (0 = apagado)
    "tope_dm_hora": 0.75,  # fracción del presupuesto por hora
    "tope_resp_hora": 0.75,
    "lectura_cada_min": [6, 12],
    # Lectura con actividad (D29): si hubo comentarios nuevos en los últimos `actividad_min` minutos o hay un
    # reel publicado hace menos de `reel_reciente_h` horas, se mira Studio más a menudo. Los mensajes siguen
    # con su ritmo de siempre: solo se ven antes los comentarios.
    "lectura_activa_min": [3, 5],
    "actividad_min": 30,
    "reel_reciente_h": 3,
    "repaso_reel_cada_h": 12,
    "reels_activos_dias": 30,
    "tope_lecturas_hora": 18,
    "velocidad_tecleo_ms": [60, 160],
    "caducidad_dias": 0,  # 0 = apagada
    "cola_alerta": 1000,
    "modo_publica": "todos",  # D43: el instructivo responde en público a cada comentario
    "inicio_desde": "",  # se rellena con la fecha de instalación (UTC) al crear la base
    "atender_historico": True,
    "landing": "",
    "utm": {"utm_source": "tiktok", "utm_medium": "dm", "utm_campaign": "bot"},
    "palabras_ofensivas": ["pendejo", "idiota", "imbécil", "estúpido", "cabrón", "puta", "mierda", "fuck", "bitch"],
    "cuentas_equipo": [],
    "emails_alerta": [],
    "niveles_altos_aceptados": False,
    "modo_prueba": True,
    "orden_pausa": False,
    "apagado": False,  # «Apagar el bot» en el panel: no abre Chrome ni escribe; sigue así tras reiniciar
    # D28: el comentario fijado lo pone la persona a mano. Fijarlo abre la página del vídeo y TikTok saca un captcha.
    "fijado_auto": False,
    # §17 · instructivo del cliente
    "orden_cola": "videos_recientes",  # D42: video más reciente primero y, dentro, más «me gusta» | llegada
    "recordatorio_cada_dias": 1,  # D35: como mucho una respuesta pública por persona cada N días (0 = nunca)
    "leer_bandeja": True,  # D37
    "bandeja_cada_min": [4, 8],
    "max_respuestas_auto": 3,  # respuestas automáticas por persona sin número; después, a una persona
    "palabras_humano": ["precio", "cuanto cuesta", "cuánto cuesta", "costo", "cuesta", "pagar", "pago", "tarjeta",
                        "reembolso", "devolucion", "devolución", "estafa", "fraude", "queja", "mentira", "no sirve",
                        "garantia", "garantía", "cuanto dura", "cuánto dura", "certificado", "horario", "requisitos"],
    "palabras_interes": ["me interesa", "interesado", "interesada", "quiero", "va que va", "claro que si",
                         "listo", "sale", "vamos", "dale", "me apunto", "estoy dentro"],
    "emails_leads": [],  # D38: vacío = a los correos de alertas
    "webhook_leads": "",  # D38: enlace para mandar cada lead a GoHighLevel u otro sistema
    "seguimiento_espera_h": 1,  # D39
    "bienvenida_seguidores": True,  # D40
    "seguidores_cada_min": [30, 60],
    "enlace_youtube": "",
}

ORDENES_COLA = ("videos_recientes", "llegada")


class ErrorConfig(ValueError):
    pass


# ---------------------------------------------------------------- rutas


def carpeta_datos(crear: bool = False) -> Path:
    """Carpeta de datos del sistema: Mac ~/Library/Application Support/BotTikTok,
    Windows %LOCALAPPDATA%\\BotTikTok. Ahí van bot.db, perfil/, capturas/, logs/, config.toml y .env."""
    propia = os.environ.get(VAR_CARPETA_DATOS)
    ruta = Path(propia) if propia else Path(user_data_dir(NOMBRE_APP, appauthor=False, roaming=False))
    if crear:
        ruta.mkdir(parents=True, exist_ok=True)
    return ruta


# ---------------------------------------------------------------- validación


def validar(clave: str, valor: Any) -> Any:
    if clave not in DEFAULTS:
        raise ErrorConfig(f"Clave de configuración desconocida: {clave!r}")
    defecto = DEFAULTS[clave]
    if isinstance(defecto, bool):
        ok = isinstance(valor, bool)
    elif isinstance(defecto, float):
        ok = isinstance(valor, (int, float)) and not isinstance(valor, bool)
    elif isinstance(defecto, int):
        ok = isinstance(valor, int) and not isinstance(valor, bool)
    else:
        ok = isinstance(valor, type(defecto))
    if not ok:
        raise ErrorConfig(f"{clave!r}: se esperaba {type(defecto).__name__}, llegó {valor!r}")
    if clave == "modo_publica" and valor not in MODOS_PUBLICA:
        raise ErrorConfig(f"'modo_publica' debe ser uno de {MODOS_PUBLICA}, llegó {valor!r}")
    if clave == "orden_cola" and valor not in ORDENES_COLA:
        raise ErrorConfig(f"'orden_cola' debe ser uno de {ORDENES_COLA}, llegó {valor!r}")
    if clave == "webhook_leads" and valor and not valor.startswith(("https://", "http://")):
        raise ErrorConfig("el enlace para mandar los leads tiene que empezar por https://")
    if clave == "enlace_youtube" and valor and not valor.startswith(("https://", "http://")):
        raise ErrorConfig("el enlace de YouTube tiene que empezar por https://")
    if clave == "nivel_ritmo" and not 1 <= valor <= 6:
        raise ErrorConfig(f"'nivel_ritmo' debe estar entre 1 y 6, llegó {valor!r}")
    if clave in RANGOS and not RANGOS[clave][0] <= valor <= RANGOS[clave][1]:
        minimo, maximo = RANGOS[clave]
        raise ErrorConfig(f"{clave!r} tiene que estar entre {minimo} y {maximo}, llegó {valor!r}")
    return valor


# Auditoría 26 nov (hallazgo 14): números que el cliente escribe en Ajustes. Sin máximo, un número absurdo se
# guardaba y el bot dejaba de atender (fecha fuera de rango en cada vuelta). Mínimo y máximo razonables:
RANGOS: dict[str, tuple[int, int]] = {
    "caducidad_dias": (0, 3650),          # 0 = sin límite; como mucho 10 años
    "recordatorio_cada_dias": (0, 365),   # 0 = nunca
    "max_respuestas_auto": (0, 50),
    "seguimiento_espera_h": (0, 720),     # como mucho 30 días
}


def leer_toml(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        return {}
    with ruta.open("rb") as f:
        valores = tomllib.load(f)
    for clave, valor in valores.items():
        validar(clave, valor)
    return valores


# ---------------------------------------------------------------- tabla config


def _ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def inicializar(conn: sqlite3.Connection, carpeta: Path) -> None:
    """Inserta los valores que falten: config.toml > defaults. No pisa lo ya guardado."""
    iniciales = dict(DEFAULTS)
    iniciales["inicio_desde"] = _ahora()
    iniciales.update(leer_toml(carpeta / "config.toml"))
    ahora = _ahora()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO config (clave, valor, actualizado_en) VALUES (?, ?, ?)",
            [(c, json.dumps(v, ensure_ascii=False), ahora) for c, v in iniciales.items()],
        )
        # D29: el tope antiguo (12) cortaba la lectura rápida. Solo se cambia si sigue con el valor antiguo.
        conn.execute("UPDATE config SET valor = '18', actualizado_en = ? WHERE clave = 'tope_lecturas_hora' "
                     "AND valor = '12'", (ahora,))


def leer(conn: sqlite3.Connection, clave: str) -> Any:
    fila = conn.execute("SELECT valor FROM config WHERE clave = ?", (clave,)).fetchone()
    if fila is None:
        if clave in DEFAULTS:
            return DEFAULTS[clave]
        raise ErrorConfig(f"Clave de configuración desconocida: {clave!r}")
    return json.loads(fila[0])


def leer_todo(conn: sqlite3.Connection) -> dict[str, Any]:
    valores = dict(DEFAULTS)
    valores.update({f[0]: json.loads(f[1]) for f in conn.execute("SELECT clave, valor FROM config")})
    return valores


def guardar(conn: sqlite3.Connection, clave: str, valor: Any) -> None:
    validar(clave, valor)
    with conn:
        conn.execute(
            "INSERT INTO config (clave, valor, actualizado_en) VALUES (?, ?, ?) "
            "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, actualizado_en = excluded.actualizado_en",
            (clave, json.dumps(valor, ensure_ascii=False), _ahora()),
        )


# ---------------------------------------------------------------- secretos


class Secretos:
    """Valores de `.env`. Se imprimen enmascarados para que nunca acaben en un log."""

    def __init__(self, valores: dict[str, str]):
        self._valores = {k: v for k, v in valores.items() if v}

    def get(self, clave: str, defecto: str | None = None) -> str | None:
        return self._valores.get(clave, defecto)

    def __contains__(self, clave: str) -> bool:
        return clave in self._valores

    def __repr__(self) -> str:
        return "Secretos(" + ", ".join(f"{k}=***" for k in sorted(self._valores)) + ")"

    __str__ = __repr__


def cargar_secretos() -> Secretos:
    ruta = carpeta_datos() / ".env"
    if not ruta.is_file():
        return Secretos({})
    return Secretos({k: v for k, v in dotenv_values(ruta, interpolate=False).items() if v is not None})


CLAVES_SECRETAS = ("SMTP_HOST", "SMTP_PUERTO", "SMTP_USUARIO", "SMTP_CONTRASENA", "SMTP_REMITENTE", "PANEL_CONTRASENA",
                   "SOPORTE_CLAVE", "PANEL_SESION")
# PANEL_CONTRASENA y SOPORTE_CLAVE: solo el resumen (pbkdf2), nunca la clave (auditoría 26 nov, hallazgo 19; una
# PANEL_CONTRASENA antigua en claro se convierte al arrancar el panel). PANEL_SESION: clave al azar con la que el panel
# firma la sesión del navegador; se cambia al pulsar «Salir», y así se cierran todas las sesiones abiertas.


def resumen_clave(clave: str, sal: str | None = None) -> str:
    """«pbkdf2$sal$resumen» de una clave (la de soporte o la del panel): quien abra el .env no puede leer la clave."""
    import hashlib
    import secrets

    sal = sal or secrets.token_hex(16)
    return "pbkdf2$" + sal + "$" + hashlib.pbkdf2_hmac("sha256", clave.encode(), sal.encode(), 200_000).hex()


def es_resumen(valor: str | None) -> bool:
    partes = (valor or "").split("$")
    return len(partes) == 3 and partes[0] == "pbkdf2"


def clave_coincide(clave: str, guardada: str | None) -> bool:
    """¿`clave` es la que dio el resumen `guardada`? Acepta ñ y tildes (se compara en bytes)."""
    import hmac

    if not clave or not es_resumen(guardada):
        return False
    return hmac.compare_digest(resumen_clave(clave, guardada.split("$")[1]).encode(), guardada.encode())


def clave_soporte_correcta(clave: str) -> bool:
    return clave_coincide(clave, cargar_secretos().get("SOPORTE_CLAVE"))


def _valor_env(valor: str) -> str:
    """Entre comillas dobles y escapado: así una contraseña con #, espacios o comillas se lee igual."""
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"') + '"'


def guardar_secretos(cambios: dict[str, str]) -> None:
    """Escribe claves en `.env` (lo usa el panel: el cliente nunca edita el archivo a mano).
    Conserva comentarios y el resto de líneas; escritura atómica; en Mac/Linux, solo legible por el usuario."""
    desconocidas = set(cambios) - set(CLAVES_SECRETAS)
    if desconocidas:
        raise ValueError(f"Claves de .env desconocidas: {sorted(desconocidas)}")
    for clave, valor in cambios.items():
        if "\n" in valor or "\r" in valor:
            raise ValueError(f"{clave}: el valor no puede tener saltos de línea")
    ruta = carpeta_datos(crear=True) / ".env"
    lineas = ruta.read_text(encoding="utf-8").splitlines() if ruta.is_file() else []
    pendientes = dict(cambios)
    salida = []
    for linea in lineas:
        clave = linea.split("=", 1)[0].strip()
        if not linea.lstrip().startswith("#") and "=" in linea and clave in pendientes:
            salida.append(f"{clave}={_valor_env(pendientes.pop(clave))}")
        else:
            salida.append(linea)
    salida += [f"{k}={_valor_env(v)}" for k, v in pendientes.items()]
    temporal = ruta.with_name(".env.nuevo")
    temporal.write_text("\n".join(salida) + "\n", encoding="utf-8")
    if os.name != "nt":
        temporal.chmod(0o600)
    os.replace(temporal, ruta)
