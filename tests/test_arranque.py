import subprocess
import sys
from pathlib import Path

import bot
from bot import config

RAIZ_APP = Path(__file__).resolve().parents[1]


def test_version_por_linea_de_comandos():
    salida = subprocess.run(
        [sys.executable, "-m", "bot", "--version"],
        cwd=RAIZ_APP,
        capture_output=True,
        text=True,
        check=True,
    )
    assert salida.stdout.strip() == f"bot-tiktok {bot.__version__}"


def test_carpeta_de_datos_por_defecto_fuera_del_codigo(monkeypatch):
    monkeypatch.delenv(config.VAR_CARPETA_DATOS, raising=False)
    datos = config.carpeta_datos()
    assert datos.name == "BotTikTok"
    assert RAIZ_APP not in datos.parents


def test_carpeta_de_datos_se_puede_cambiar_y_se_crea(monkeypatch, tmp_path):
    destino = tmp_path / "con espacios" / "datos"
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(destino))
    assert config.carpeta_datos(crear=True) == destino
    assert destino.is_dir()


def test_arrancar_crea_bot_db_en_la_carpeta_de_datos(tmp_path):
    import os

    destino = tmp_path / "datos con espacios"
    entorno = {**os.environ, config.VAR_CARPETA_DATOS: str(destino)}
    subprocess.run([sys.executable, "-m", "bot", "--solo-preparar"], cwd=RAIZ_APP, env=entorno, check=True, capture_output=True)
    assert (destino / "bot.db").is_file()
    assert not list(RAIZ_APP.glob("*.db"))
