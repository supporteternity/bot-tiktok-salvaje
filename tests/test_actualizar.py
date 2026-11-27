import sqlite3

import pytest

from bot import actualizar, config, db


@pytest.fixture
def sin_servicios(monkeypatch):
    llamadas = []
    monkeypatch.setattr(actualizar, "_parar_servicios", lambda: llamadas.append("parar"))
    monkeypatch.setattr(actualizar, "_registrar_aqui", lambda: llamadas.append("registrar"))
    monkeypatch.setattr(actualizar, "_arrancar_servicios", lambda: llamadas.append("arrancar"))
    monkeypatch.setattr(actualizar.instalador, "hay_chrome", lambda: True)
    return llamadas


# ---------------------------------------------------------------- auditoría 26 nov (hallazgo 9)
# Una actualización que falla a medias no puede dejar el bot apagado (en Windows, tareas desactivadas para siempre)
# sin que nadie se entere: lo que falle antes de parar no para nada, y lo que falle después vuelve a arrancar.


def test_sin_internet_para_descargar_chrome_no_para_nada(conn, sin_servicios, monkeypatch):
    import subprocess

    def sin_red():
        raise subprocess.CalledProcessError(1, ["playwright", "install"])

    monkeypatch.setattr(actualizar.instalador, "hay_chrome", lambda: False)
    monkeypatch.setattr(actualizar.instalador, "instalar_chrome", sin_red)
    assert actualizar.actualizar() == 1
    assert sin_servicios == []  # el bot sigue funcionando con la versión que había


def test_si_falla_la_base_se_deja_como_estaba_y_vuelve_a_arrancar_la_version_anterior(conn, sin_servicios, monkeypatch):
    config.guardar(conn, "landing", "https://antes.com")
    conn.close()

    def migracion_a_medias():
        c = db.conectar()
        config.guardar(c, "landing", "https://a-medias.com")  # una parte se aplicó…
        c.close()
        raise sqlite3.OperationalError("disk I/O error")  # …y otra no

    monkeypatch.setattr(actualizar.db, "preparar", migracion_a_medias)
    assert actualizar.actualizar() == 1
    assert sin_servicios == ["parar", "arrancar"]
    c = db.conectar()
    assert config.leer(c, "landing") == "https://antes.com"  # la base, como antes de actualizar
    c.close()


def test_si_falla_el_arranque_automatico_vuelve_a_arrancar(conn, sin_servicios, monkeypatch):
    def falla():
        sin_servicios.append("registrar")
        raise OSError("launchctl: Input/output error")

    monkeypatch.setattr(actualizar, "_registrar_aqui", falla)
    assert actualizar.actualizar() == 1
    assert sin_servicios == ["parar", "registrar", "arrancar"]


def test_restaurar_que_falla_tambien_vuelve_a_arrancar(conn, sin_servicios, monkeypatch):
    copia = actualizar.copia_seguridad()

    def falla():
        sin_servicios.append("registrar")
        raise OSError("schtasks: acceso denegado")

    monkeypatch.setattr(actualizar, "_registrar_aqui", falla)
    assert actualizar.restaurar(copia) == 1
    assert sin_servicios == ["parar", "registrar", "arrancar"]


def test_actualizar_copia_la_base_para_migra_y_registra(conn, sin_servicios):
    config.guardar(conn, "landing", "https://antes.com")
    assert actualizar.actualizar() == 0
    copias = list((config.carpeta_datos() / "copias").glob("bot-*-actualizar.db"))
    assert len(copias) == 1
    with sqlite3.connect(copias[0]) as c:
        assert c.execute("SELECT valor FROM config WHERE clave='landing'").fetchone()[0] == '"https://antes.com"'
    assert sin_servicios == ["parar", "registrar"]


def test_restaurar_devuelve_la_base_y_guarda_la_que_habia(conn, sin_servicios):
    config.guardar(conn, "landing", "https://antes.com")
    copia = actualizar.copia_seguridad()
    config.guardar(conn, "landing", "https://despues.com")
    conn.close()
    assert actualizar.restaurar(copia) == 0
    c = db.conectar()
    assert config.leer(c, "landing") == "https://antes.com"
    c.close()
    assert list((config.carpeta_datos() / "copias").glob("bot-*-antes-de-restaurar.db"))


def test_restaurar_copia_inexistente(conn, sin_servicios, tmp_path):
    assert actualizar.restaurar(tmp_path / "no.db") == 1
    assert sin_servicios == []
