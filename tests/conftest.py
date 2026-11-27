from datetime import UTC, datetime, timedelta

import pytest

from bot import config, db

AHORA = datetime(2026, 11, 24, 18, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(tmp_path / "datos"))
    c = db.preparar(sembrar=False)
    config.guardar(c, "cuenta", "academia")
    # Las pruebas anteriores al instructivo (§17) se escribieron con estos valores por defecto.
    config.guardar(c, "modo_publica", "solo_cerrados")
    config.guardar(c, "orden_cola", "llegada")
    config.guardar(c, "inicio_desde", (AHORA - timedelta(days=30)).isoformat())
    yield c
    c.close()


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def meter_comentario(conn, cid, usuario, reel="r1", texto="info", hace_min=10, estado="en_cola", user_id=None):
    """Inserta reel + contacto (si faltan) + comentario. Devuelve el id del comentario."""
    user_id = user_id or f"u:{usuario}"
    with conn:
        conn.execute("INSERT OR IGNORE INTO reels (id, url) VALUES (?, ?)", (reel, f"https://www.tiktok.com/@academia/video/{reel}"))
        conn.execute("INSERT OR IGNORE INTO contactos (user_id, usuario) VALUES (?, ?)", (user_id, usuario))
        conn.execute(
            "INSERT INTO comentarios (id, reel_id, user_id, texto, comentado_en, detectado_en, estado, actualizado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, reel, user_id, texto, iso(AHORA - timedelta(minutes=hace_min)), iso(AHORA), estado, iso(AHORA)),
        )
    return cid
