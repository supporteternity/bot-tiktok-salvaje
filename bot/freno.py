"""Freno (PLAN-OFICIAL §7.3). Ante la duda, parar; nunca resolver nada solo.

| Situación                                        | Qué hace                                         |
|--------------------------------------------------|--------------------------------------------------|
| Aviso (demasiado rápido, restringido, desconocido)| baja 1 nivel, espera 24 h                        |
| 2.º aviso en 7 días                              | baja otro nivel, espera 48 h                     |
| 3.er aviso en 7 días                             | parado_revision (no vuelve solo)                 |
| Captcha o verificación                           | parado_revision inmediato                        |
| Sesión cerrada                                   | sesion_cerrada                                   |
| 5 errores técnicos seguidos                      | pausa 30 min; la 3.ª pausa en 24 h → revisión    |

"Esperar" = ninguna acción ni lectura en TikTok (lo hace cumplir el bucle).
Cada caso devuelve una `Alerta` para el email (la envía alertas.py, tarea F2).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot import config, ritmo
from bot import redaccion as r
from bot.ritmo import _fecha, iso

AVISOS_DE_RITMO = ("demasiado_rapido", "restringido", "desconocido")
AVISOS_DE_PERSONA = ("captcha", "verificacion")
ERRORES_SEGUIDOS = 5
PAUSA_ERRORES = timedelta(minutes=30)
PAUSAS_ERRORES_PARA_REVISION = 3
ESPERAS = {1: timedelta(hours=24), 2: timedelta(hours=48)}


@dataclass(frozen=True)
class Alerta:
    tipo: str  # frenado | necesita_persona
    asunto: str
    cuerpo: str
    captura: str | None = None


@dataclass(frozen=True)
class Resultado:
    modo: str  # activo | frenado | parado_revision | sesion_cerrada
    hasta: datetime | None = None
    alerta: Alerta | None = None


PASOS_REVISION = (
    "Qué tienes que hacer:\n"
    "1. Ve a la computadora donde corre el bot y abre la ventana de Chrome del bot "
    "(está minimizada, en el Dock o en la barra de tareas).\n"
    "2. Resuelve tú lo que pide TikTok.\n"
    "3. Abre «Panel del bot» y pulsa «Reanudar»."
)
PASOS_SESION = (
    "Qué tienes que hacer:\n"
    "1. En la computadora del bot, abre «Iniciar sesión del bot» (está en el Escritorio).\n"
    "2. Entra a TikTok una sola vez, con el código QR desde la app del celular. "
    "Si TikTok dice que hay demasiados intentos, espera unas horas y no insistas.\n"
    "3. Cierra esa ventana, abre «Panel del bot» y pulsa «Reanudar»."
)


def _con_captura(captura: str | None) -> str:
    return "\n\nVa adjunta una captura de lo que se veía en la pantalla." if captura else ""


class Freno:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.errores_seguidos = 0
        # ¿Alguno de los errores seguidos fue al actuar (privado, respuesta)? Entonces una lectura buena no basta
        # para darlos por arreglados: solo un envío bueno (27 nov).
        self.error_al_actuar = False

    # ------------------------------------------------------------ guardar

    def _evento(self, tipo: str, detalle: str, captura: str | None, ahora: datetime) -> None:
        self.conn.execute(
            "INSERT INTO eventos (tipo, detalle, captura, creado_en) VALUES (?, ?, ?, ?)",
            (tipo, detalle, captura, iso(ahora)),
        )

    def _poner_modo(self, modo: str, hasta: datetime | None, mensaje: str) -> None:
        self.conn.execute(
            "UPDATE estado_bot SET modo = ?, frenado_hasta = ?, mensaje = ? WHERE id = 1",
            (modo, iso(hasta) if hasta else None, mensaje),
        )

    def _contar(self, tipo: str, desde: datetime) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM eventos WHERE tipo = ? AND creado_en > ?", (tipo, iso(desde))
        ).fetchone()[0]

    # ------------------------------------------------------------ casos

    def aviso(self, clase: str, detalle: str, captura: str | None, ahora: datetime) -> Resultado:
        """`clase`: demasiado_rapido | restringido | desconocido | captcha | verificacion | sesion_cerrada."""
        with self.conn:
            if clase == "sesion_cerrada":
                self._evento("sesion_cerrada", detalle, captura, ahora)
                self._poner_modo("sesion_cerrada", None, "Sesión de TikTok cerrada: hay que iniciarla a mano")
                return Resultado("sesion_cerrada", None, Alerta(
                    "necesita_persona", r.asunto(r.TE_NECESITA, "Te necesita: vuelve a iniciar sesión en TikTok"),
                    "TikTok cerró la sesión del bot y el bot se paró. No hará nada hasta que vuelvas a entrar.\n\n"
                    f"{PASOS_SESION}{_con_captura(captura)}{r.tecnico(detalle)}", captura))

            if clase in AVISOS_DE_PERSONA:
                self._evento("captcha", f"{clase}: {detalle}", captura, ahora)
                self._poner_modo("parado_revision", None, f"Parado: TikTok pide {clase}")
                return Resultado("parado_revision", None, Alerta(
                    "necesita_persona",
                    r.asunto(r.TE_NECESITA, "Te necesita: TikTok pide " + ("un captcha" if clase == "captcha" else "verificar la cuenta")),
                    f"TikTok pidió {r.aviso(clase)} y el bot se paró. No lo resuelve solo: lo tiene que hacer una persona.\n\n"
                    f"{PASOS_REVISION}{_con_captura(captura)}{r.tecnico(detalle)}", captura))

            # aviso de ritmo (o desconocido)
            self._evento("aviso_tiktok", f"{clase}: {detalle}", captura, ahora)
            avisos_7d = self._contar("aviso_tiktok", ahora - timedelta(days=7))
            self.conn.execute("UPDATE estado_bot SET frenos_7d = ? WHERE id = 1", (avisos_7d,))

            if avisos_7d >= 3:
                self._poner_modo("parado_revision", None, f"Parado: {avisos_7d} avisos de TikTok en 7 días")
                return Resultado("parado_revision", None, Alerta(
                    "necesita_persona", r.asunto(r.TE_NECESITA, f"Te necesita: TikTok avisó {avisos_7d} veces esta semana"),
                    f"TikTok mostró avisos {avisos_7d} veces en los últimos 7 días (el último: {r.aviso(clase)}). "
                    "Para cuidar la cuenta, el bot se paró y no vuelve solo.\n\n"
                    "Qué tienes que hacer:\n"
                    "1. Habla con soporte antes de reanudar: puede que haya que bajar el ritmo o esperar unos días.\n"
                    "2. Cuando lo decidan, abre «Panel del bot» y pulsa «Reanudar»."
                    f"{_con_captura(captura)}{r.tecnico(detalle)}", captura))

        nivel = config.leer(self.conn, "nivel_ritmo")
        if nivel > 1:
            ritmo.cambiar_nivel(self.conn, nivel - 1, ahora, f"aviso de TikTok ({clase})")
        hasta = ahora + ESPERAS[avisos_7d]
        with self.conn:
            # «nivel previo» sirve para deshacerlo si soporte ve que fue una falsa alarma.
            self._evento("freno", f"{clase}: espera hasta {iso(hasta)} · nivel previo {nivel}", captura, ahora)
            self._poner_modo("frenado", hasta, f"TikTok mostró {r.aviso(clase)}")
        return Resultado("frenado", hasta, Alerta(
            "frenado", r.asunto(r.SOLO_AVISO, f"Frenado por TikTok, vuelve solo {r.cuando(hasta, ahora, con_zona=False)}"),
            f"TikTok mostró {r.aviso(clase)}. Para cuidar la cuenta, el bot baja el ritmo al nivel {max(1, nivel - 1)} "
            f"y no hará nada en TikTok hasta {r.cuando(hasta, ahora)}. Después sigue solo.\n\n"
            f"No tienes que hacer nada.{r.tecnico(detalle)}",
            captura))

    def error_tecnico(self, detalle: str, captura: str | None, ahora: datetime,
                      al_actuar: bool = True) -> Resultado | None:
        """Un error que no es de TikTok (selector, red...). None si aún no toca frenar.
        `al_actuar=False`: fallo al leer o al cargar una página fuera de un envío (p. ej. la red tardó más de 20 s)."""
        self.errores_seguidos += 1
        self.error_al_actuar |= al_actuar
        with self.conn:
            self._evento("error", detalle, captura, ahora)
        if self.errores_seguidos < ERRORES_SEGUIDOS:
            return None
        self.errores_seguidos, self.error_al_actuar = 0, False
        with self.conn:
            self._evento("pausa_errores", detalle, captura, ahora)
            pausas = self._contar("pausa_errores", ahora - timedelta(hours=24))
            if pausas >= PAUSAS_ERRORES_PARA_REVISION:
                self._poner_modo("parado_revision", None, "Parado: errores técnicos repetidos")
                return Resultado("parado_revision", None, Alerta(
                    "necesita_persona", r.asunto(r.TE_NECESITA, "Te necesita: falla una y otra vez, avisa a soporte"),
                    f"El bot falló muchas veces en las últimas 24 horas ({pausas} pausas por errores) y se paró. "
                    "Puede que TikTok haya cambiado su página.\n\n"
                    "Qué tienes que hacer:\n"
                    "1. Avisa a soporte y reenvíales este correo.\n"
                    "2. No pulses «Reanudar» hasta que soporte te lo diga."
                    f"{_con_captura(captura)}{r.tecnico(detalle)}", captura))
            hasta = ahora + PAUSA_ERRORES
            self._poner_modo("frenado", hasta, "Pausa de 30 min por errores seguidos")
        return Resultado("frenado", hasta, Alerta(
            "frenado", r.asunto(r.SOLO_AVISO, f"Pausa de 30 min por errores · vuelve solo a las {r.hora(hasta)}"),
            f"El bot falló {ERRORES_SEGUIDOS} veces seguidas, así que descansa 30 minutos y lo vuelve a intentar "
            f"{r.cuando(hasta, ahora)}.\n\n"
            "No tienes que hacer nada. Si te llegan varios correos como este el mismo día, avisa a soporte."
            f"{r.tecnico(detalle)}", captura))

    def accion_ok(self) -> None:
        self.errores_seguidos, self.error_al_actuar = 0, False

    def lectura_ok(self) -> None:
        """Una lectura de TikTok salió bien: los cortes de red sueltos de antes ya no cuentan como «seguidos»
        (visto el 26–27 nov: páginas que tardan más de 20 s de vez en cuando). Si alguno de esos errores fue al
        enviar un privado o una respuesta, siguen contando: leer bien no arregla un envío que falla."""
        if not self.error_al_actuar:
            self.errores_seguidos = 0

    # ------------------------------------------------------------ estado

    def estado(self, ahora: datetime) -> Resultado:
        """Modo actual. Si la espera de un freno ya pasó, vuelve a `activo` (y lo apunta)."""
        e = self.conn.execute("SELECT modo, frenado_hasta FROM estado_bot WHERE id = 1").fetchone()
        if e["modo"] == "frenado" and e["frenado_hasta"] and ahora >= _fecha(e["frenado_hasta"]):
            with self.conn:
                self._evento("reanudar", "fin de la espera del freno", None, ahora)
                self._poner_modo("activo", None, "Activo")
            return Resultado("activo")
        hasta = _fecha(e["frenado_hasta"]) if e["frenado_hasta"] else None
        return Resultado(e["modo"], hasta)

    def quitar_falsa_alarma(self, ahora: datetime) -> bool:
        """Soporte (con su clave, desde el panel) vio que el último freno fue una falsa alarma: TikTok no avisó
        de nada. Deshace ese freno: vuelve a trabajar, al nivel de antes, y el aviso deja de contar para
        «3 avisos en 7 días». Solo vale con el bot en `frenado` (un captcha o una revisión no se quitan así)."""
        if self.conn.execute("SELECT modo FROM estado_bot WHERE id = 1").fetchone()[0] != "frenado":
            return False
        aviso = self.conn.execute("SELECT id FROM eventos WHERE tipo = 'aviso_tiktok' ORDER BY id DESC LIMIT 1").fetchone()
        freno = self.conn.execute("SELECT detalle FROM eventos WHERE tipo = 'freno' ORDER BY id DESC LIMIT 1").fetchone()
        previo = re.search(r"nivel previo (\d+)", freno[0]) if freno else None
        with self.conn:
            if aviso:
                self.conn.execute("UPDATE eventos SET tipo = 'aviso_falso' WHERE id = ?", (aviso[0],))
            avisos_7d = self._contar("aviso_tiktok", ahora - timedelta(days=7))
            self.conn.execute("UPDATE estado_bot SET frenos_7d = ? WHERE id = 1", (avisos_7d,))
            self._evento("reanudar", "soporte quitó el freno: falsa alarma", None, ahora)
            self._poner_modo("activo", None, "Activo")
        if previo and int(previo.group(1)) != config.leer(self.conn, "nivel_ritmo"):
            ritmo.cambiar_nivel(self.conn, int(previo.group(1)), ahora, "falsa alarma: vuelve al nivel de antes")
        self.errores_seguidos, self.error_al_actuar = 0, False
        return True

    def reanudar_manual(self, ahora: datetime) -> None:
        """Una persona pulsa «Reanudar» en el panel (tras resolver captcha, sesión, etc.)."""
        with self.conn:
            self._evento("reanudar", "reanudado a mano desde el panel", None, ahora)
            self._poner_modo("activo", None, "Activo")
        self.errores_seguidos, self.error_al_actuar = 0, False
