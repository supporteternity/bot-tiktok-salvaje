"""Windows arranca los servicios con pythonw: sin consola, sys.stdout y sys.stderr son None."""

import sys

import pytest

from bot import config, navegador
from bot.__main__ import salida_a_archivo


def test_sin_consola_la_salida_va_a_los_logs_y_uvicorn_arranca(tmp_path, monkeypatch):
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    salida_a_archivo("panel")
    try:
        import uvicorn
        from uvicorn.logging import DefaultFormatter

        DefaultFormatter()  # aquí se caía: sys.stdout.isatty() con stdout = None
        uvicorn.Config(app=lambda *a: None).configure_logging()
        print("hola")
        print("error", file=sys.stderr)
    finally:
        sys.stdout.close()
        sys.stderr.close()
    assert "hola" in (tmp_path / "logs" / "panel.salida.log").read_text(encoding="utf-8")
    assert "error" in (tmp_path / "logs" / "panel.errores.log").read_text(encoding="utf-8")


def test_con_consola_no_toca_nada(tmp_path, monkeypatch):
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(tmp_path))
    antes = sys.stdout
    salida_a_archivo("bot")
    assert sys.stdout is antes and not (tmp_path / "logs").exists()


class _NavFalso:
    def __init__(self, ocupado_veces):
        self.ocupado_veces, self.intentos = ocupado_veces, 0

    def abrir(self):
        self.intentos += 1
        if self.intentos <= self.ocupado_veces:
            raise navegador.PerfilOcupado("ocupado")
        return self


def test_iniciar_sesion_espera_a_que_el_chrome_del_servicio_suelte_el_perfil():
    nav = _NavFalso(ocupado_veces=2)
    assert navegador.abrir_con_espera(nav, espera_perfil_s=5, pausa_s=0.01) is nav and nav.intentos == 3


def test_sin_espera_el_perfil_ocupado_se_avisa_enseguida():
    with pytest.raises(navegador.PerfilOcupado):
        navegador.abrir_con_espera(_NavFalso(ocupado_veces=1), espera_perfil_s=0)
