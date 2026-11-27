"""Cómo se escriben los correos del bot: asuntos fáciles de encontrar en la bandeja, horas en lenguaje normal
y nombres claros de los avisos de TikTok. Español de México, sin jerga (lo técnico va al final, para soporte)."""

from __future__ import annotations

from datetime import datetime

from bot.ritmo import ZONA

REMITENTE = "Bot de TikTok"

# Un icono por gravedad, siempre al principio del asunto, para verlo de un vistazo entre muchos correos.
TE_NECESITA = "🔴"
SOLO_AVISO = "🟡"
REVISAR = "🟠"
TODO_BIEN = "✅"
RESUMEN = "📊"

AVISOS = {
    "demasiado_rapido": "que vas demasiado rápido",
    "restringido": "que limitó la cuenta por un tiempo",
    "desconocido": "un aviso que el bot no reconoce",
    "captcha": "un captcha (la prueba de «no soy un robot»)",
    "verificacion": "verificar la cuenta",
}

PANEL = "Abre «Panel del bot» en la computadora donde corre el bot."

_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre")
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def asunto(icono: str, texto: str) -> str:
    return f"{icono} {REMITENTE} · {texto}"


def aviso(clase: str) -> str:
    return AVISOS.get(clase, AVISOS["desconocido"])


def hora(momento: datetime) -> str:
    """«7:06 p. m.» en hora de Utah."""
    local = momento.astimezone(ZONA)
    h = local.hour % 12 or 12
    return f"{h}:{local.minute:02d} {'a. m.' if local.hour < 12 else 'p. m.'}"


def cuando(momento: datetime, ahora: datetime, con_zona: bool = True) -> str:
    """«hoy a las 7:06 p. m.», «mañana a las 9:00 a. m.» o «el jueves 26 de noviembre a las…» (hora de Utah)."""
    dias = (momento.astimezone(ZONA).date() - ahora.astimezone(ZONA).date()).days
    if dias == 0:
        dia = "hoy"
    elif dias == 1:
        dia = "mañana"
    else:
        dia = "el " + fecha(momento)
    return f"{dia} a las {hora(momento)}" + (" (hora de Utah)" if con_zona else "")


def fecha(momento: datetime) -> str:
    """«jueves 26 de noviembre» (hora de Utah)."""
    local = momento.astimezone(ZONA)
    return f"{_DIAS[local.weekday()]} {local.day} de {_MESES[local.month - 1]}"


def duracion(segundos: float) -> str:
    """«menos de 1 min», «20 min», «3 h», «2 días»."""
    minutos = int(segundos // 60)
    if minutos < 1:
        return "menos de 1 min"
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos / 60
    if horas < 48:
        return f"{horas:.0f} h" if horas >= 10 else f"{horas:.1f} h".replace(".0 h", " h")
    return f"{horas / 24:.0f} días"


def plural(n: int, una: str, varias: str) -> str:
    return f"{n} {una if n == 1 else varias}"


def tecnico(detalle: str | None) -> str:
    """Lo técnico, al final y marcado para soporte (vacío si no hay nada)."""
    detalle = (detalle or "").strip()
    return f"\n\n—\nDetalle técnico (para soporte): {detalle}" if detalle else ""
