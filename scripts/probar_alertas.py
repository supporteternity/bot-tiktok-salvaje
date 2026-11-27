"""Prueba F2: fuerza las 5 alertas de §8 y las envía por SMTP real.

Usa una base de datos TEMPORAL (no toca bot.db) pero lee el .env y los destinatarios de la
carpeta de datos del bot. Uso (desde BOT-TIKTOK-APP):
    uv run python scripts/probar_alertas.py destinatario@ejemplo.com
"""

import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import alertas, config, db  # noqa: E402
from bot.freno import Freno  # noqa: E402
from bot.ritmo import iso  # noqa: E402


NOMBRES = ("Bot frenado", "Necesita a una persona", "Cola grande", "Bot sin actividad", "Resumen diario")


def forzar_cinco(conn, ahora: datetime) -> list[bool]:
    """Fuerza las 5 alertas de §8 en esa base (temporal) y las envía. Devuelve si salió cada una."""
    config.guardar(conn, "cola_alerta", 0)
    r: list[bool] = []

    r.append(alertas.enviar_alerta(conn, Freno(conn).aviso("demasiado_rapido", "[PRUEBA]", None, ahora).alerta, ahora))
    r.append(alertas.enviar_alerta(conn, Freno(conn).aviso("captcha", "[PRUEBA]", None, ahora).alerta, ahora))
    with conn:
        conn.execute("INSERT INTO reels (id, url) VALUES ('prueba', 'https://www.tiktok.com')")
        conn.execute("INSERT INTO contactos (user_id, usuario) VALUES ('u:prueba', 'prueba')")
        conn.execute("INSERT INTO comentarios (id, reel_id, user_id, comentado_en, detectado_en, actualizado_en) "
                     "VALUES ('prueba', 'prueba', 'u:prueba', ?, ?, ?)", (iso(ahora),) * 3)
    r.append(alertas.revisar_cola(conn, ahora))
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'activo', ultimo_latido = ?", (iso(ahora - timedelta(minutes=20)),))
    r.append(alertas.revisar_latido(conn, ahora))
    nueve = ahora.astimezone(alertas.ZONA).replace(hour=10)
    r.append(alertas.revisar_resumen(conn, nueve.astimezone(UTC)))
    for i, (nombre, ok) in enumerate(zip(NOMBRES, r), 1):
        print(f"{i}/5 {nombre}…", "enviado" if ok else "NO enviado")
    return r


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    env_real = config.carpeta_datos() / ".env"
    if not env_real.is_file():
        print(f"Falta {env_real} con los datos SMTP (ver .env.ejemplo).")
        return 1
    temporal = Path(tempfile.mkdtemp(prefix="prueba-alertas-"))
    shutil.copy(env_real, temporal / ".env")
    os.environ[config.VAR_CARPETA_DATOS] = str(temporal)
    conn = db.preparar()
    config.guardar(conn, "emails_alerta", sys.argv[1:])
    ahora = datetime.now(UTC)
    resultados = forzar_cinco(conn, ahora)
    conn.close()
    shutil.rmtree(temporal, ignore_errors=True)
    print("Revisa la bandeja de entrada (y spam): deberían llegar 5 emails.")
    return 0 if all(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
