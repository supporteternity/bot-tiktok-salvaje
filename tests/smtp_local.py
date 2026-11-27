"""Servidor de correo REAL pero local (aiosmtpd) para las pruebas de F2: STARTTLS con certificado de una CA
de prueba (trustme) y usuario/contraseña. Nada sale a internet. Guarda los emails recibidos."""

from __future__ import annotations

import email
import socket
import ssl
from dataclasses import dataclass, field

import trustme
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

USUARIO, CLAVE = "alertas.academia@gmail.com", "abcdefghijklmnop"


@dataclass
class _Buzon:
    mensajes: list = field(default_factory=list)

    async def handle_DATA(self, server, session, envelope):
        self.mensajes.append(email.message_from_bytes(envelope.content))
        return "250 OK"


def _autenticar(server, session, envelope, mechanism, auth_data):
    ok = isinstance(auth_data, LoginPassword) and auth_data.login.decode() == USUARIO and auth_data.password.decode() == CLAVE
    return AuthResult(success=ok, handled=False)  # handled=False: el servidor responde 535 como Gmail


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServidorLocal:
    def __init__(self):
        self.ca = trustme.CA()
        servidor_tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ca.issue_cert("localhost", "127.0.0.1").configure_cert(servidor_tls)
        self.buzon = _Buzon()
        self.puerto = _puerto_libre()
        self.controlador = Controller(
            self.buzon, hostname="127.0.0.1", port=self.puerto, server_hostname="localhost",
            tls_context=servidor_tls, require_starttls=True, auth_require_tls=True, authenticator=_autenticar,
        )

    def contexto_cliente(self) -> ssl.SSLContext:
        """Como el de verdad (verifica certificado y nombre), pero confiando en la CA de prueba."""
        ctx = ssl.create_default_context()
        self.ca.configure_trust(ctx)
        return ctx

    def datos(self, clave: str = CLAVE) -> dict:
        return {"SMTP_HOST": "localhost", "SMTP_PUERTO": str(self.puerto), "SMTP_USUARIO": USUARIO,
                "SMTP_CONTRASENA": clave, "SMTP_REMITENTE": USUARIO}

    def __enter__(self) -> ServidorLocal:
        self.controlador.start()
        return self

    def __exit__(self, *_):
        self.controlador.stop()

    @property
    def mensajes(self):
        return self.buzon.mensajes
