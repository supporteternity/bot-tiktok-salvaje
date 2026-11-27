"""Interfaz entre el ejecutor y las pantallas de TikTok (PLAN-OFICIAL §9).

El ejecutor (máquina de estados) solo habla con este contrato. La implementación real con Playwright
llega en D2/D3 (perfil.py, chat.py, reel.py); los tests usan una implementación simulada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class InfoPerfil:
    existe: bool
    puede_mensaje: bool  # hay botón "Mensaje" utilizable
    user_id: str | None = None  # ID interno estable de TikTok, si se pudo leer
    nombre: str | None = None  # nombre visible
    te_sigue: bool | None = None  # ¿sigue a la cuenta del bot? None = no se pudo saber


ResultadoEnvio = Literal["enviado", "cerrado", "dudoso", "ya_hablado", "bloqueado"]  # ya_hablado: D36, no se envió nada
# bloqueado (27 nov): TikTok contestó al enviar que no lo deja pasar (código 7195, «sending_ban»). No le llegó.


@dataclass(frozen=True)
class ChatEnLista:
    """Una conversación de la lista de mensajes (§17 D37)."""

    conv_id: str
    nombre: str  # nombre visible (la lista no enseña el @usuario)
    vista_previa: str  # último mensaje, tal como sale en la lista
    solicitud: bool = False  # está en «Solicitudes de mensajes»


@dataclass(frozen=True)
class MensajeChat:
    propio: bool  # lo envió la cuenta del bot (o alguien del equipo desde ella)
    texto: str


@dataclass(frozen=True)
class ChatLeido:
    usuario: str | None  # @usuario de la persona (None si no se pudo leer)
    mensajes: tuple[MensajeChat, ...]
    aceptada: bool = False  # era una solicitud y se aceptó ahora
ResultadoRespuesta = Literal["ok", "no_disponible"]
ResultadoFijado = Literal["fijado", "error"]


class AvisoTikTok(Exception):
    """TikTok muestra un aviso, captcha, verificación o cierra la sesión (lo detecta el vigía).

    `clase`: demasiado_rapido | restringido | desconocido | captcha | verificacion | sesion_cerrada
    """

    def __init__(self, clase: str, detalle: str = "", captura: str | None = None):
        super().__init__(f"{clase}: {detalle}")
        self.clase, self.detalle, self.captura = clase, detalle, captura


class ErrorPantalla(Exception):
    """Fallo técnico (no encuentra un elemento, no carga...). Lleva captura si la hay."""

    def __init__(self, detalle: str, captura: str | None = None):
        super().__init__(detalle)
        self.detalle, self.captura = detalle, captura


class Pantallas(Protocol):
    def abrir_perfil(self, usuario: str) -> InfoPerfil: ...

    def enviar_dm(self, usuario: str, texto: str, simulado: bool, solo_si_nuevo: bool = False) -> ResultadoEnvio:
        """Abre el chat, escribe a ritmo humano y (si no es simulado) envía y verifica que aparece.
        `solo_si_nuevo` (D36): si ya hay mensajes nuestros en el chat no escribe nada y devuelve «ya_hablado»."""

    def leer_bandeja(self) -> list[ChatEnLista]:
        """Lista de conversaciones (y solicitudes de mensajes), sin abrir ninguna (§17 D37)."""

    def leer_chat(self, chat: ChatEnLista, aceptar: bool) -> ChatLeido:
        """Abre esa conversación y lee sus mensajes. Si es una solicitud y `aceptar`, la acepta."""

    def seguidores_nuevos(self) -> list[str]:
        """@usuarios del apartado «Seguidores» de las notificaciones, del más nuevo al más viejo (§17 D40)."""

    def dm_ya_enviado(self, usuario: str) -> bool | None:
        """Tras un corte en `dm_enviando`: ¿hay ya un mensaje nuestro en ese chat? None = no se puede comprobar."""

    def responder(self, reel_id: str, comentario_id: str, texto: str, simulado: bool) -> ResultadoRespuesta:
        """Responde en público colgando del comentario indicado y verifica que aparece."""

    def publicar_fijado(self, reel_id: str, texto: str, simulado: bool) -> ResultadoFijado:
        """Publica el comentario fijado en el reel y lo fija arriba (modo solo_cerrados)."""

    def en_blanco(self) -> None:
        """Deja el navegador en una pestaña en blanco (durante frenos y descansos: nada en TikTok)."""

    def autoprueba(self) -> tuple[bool, str]:
        """Abre cada pantalla SIN actuar y comprueba que encuentra los selectores. (ok, detalle)"""


class Detector(Protocol):
    def leer(self, ahora) -> int:
        """Una lectura de comentarios nuevos (Studio y, si toca, repaso de reels). Devuelve cuántos nuevos guardó."""
