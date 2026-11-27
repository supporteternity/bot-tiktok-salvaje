"""El panel con mucho historial (un año de un cliente con ~300 comentarios al día). Medido el 27 nov con una base
de ese tamaño: el CSV del Registro tardaba ~170 s, Reels ~3 s y Registro ~1 s, porque cada fila volvía a recorrer
tablas enteras (envíos y eventos) sin índice. Aquí, una base más pequeña pero con la misma forma."""

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from bot import config
from bot.ritmo import iso
from conftest import AHORA
from panel.app import crear_app

CONTRASENA = "clave-panel"
COMENTARIOS, EVENTOS, ENVIOS, REELS = 20_000, 3_000, 3_000, 60


@pytest.fixture
def cliente_grande(conn):
    (config.carpeta_datos() / ".env").write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    with conn:
        conn.executemany("INSERT INTO reels (id, url) VALUES (?, ?)", [(f"r{i}", f"https://x/r{i}") for i in range(REELS)])
        conn.executemany("INSERT INTO contactos (user_id, usuario) VALUES (?, ?)",
                         [(f"u{i}", f"persona{i}") for i in range(COMENTARIOS // 2)])
        conn.executemany(
            "INSERT INTO comentarios (id, reel_id, user_id, texto, comentado_en, detectado_en, estado, actualizado_en, "
            "ultimo_error) VALUES (?, ?, ?, 'info', ?, ?, ?, ?, ?)",
            [(f"c{i}", f"r{i % REELS}", f"u{i // 2}", iso(AHORA - timedelta(minutes=i)), iso(AHORA),
              "completado" if i % 7 else "error_final", iso(AHORA), None if i % 7 else "falló")
             for i in range(COMENTARIOS)])
        conn.executemany("INSERT INTO eventos (tipo, detalle, captura, creado_en) VALUES ('error', ?, ?, ?)",
                         [(f"c{i * 7}: falló", f"/capturas/c{i * 7}.png", iso(AHORA)) for i in range(EVENTOS)])
        conn.executemany("INSERT INTO envios (user_id, usuario, tipo, estado, creado_en, listo_desde, actualizado_en) "
                         "VALUES (?, ?, 'dm_manual', 'enviado', ?, ?, ?)",
                         [(f"u{i}", f"persona{i}", iso(AHORA), iso(AHORA), iso(AHORA)) for i in range(ENVIOS)])
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    assert c.post("/entrar", data={"contrasena": CONTRASENA}, follow_redirects=False).status_code == 303
    return c


def _segundos(cliente, ruta):
    t = time.perf_counter()
    r = cliente.get(ruta)
    assert r.status_code == 200
    return time.perf_counter() - t, r


def test_registro_csv_con_mucho_historial_sale_enseguida_y_completo(cliente_grande):
    tarda, r = _segundos(cliente_grande, "/registro.csv")
    assert tarda < 3, f"el CSV tardó {tarda:.1f} s"
    assert r.text.count("\n") == COMENTARIOS + 1  # cabecera + todas las filas, nada se recorta


def test_registro_y_reels_con_mucho_historial_cargan_rapido(cliente_grande):
    tarda, r = _segundos(cliente_grande, "/registro")
    assert tarda < 1.5, f"el Registro tardó {tarda:.1f} s"
    assert "c0.png" in r.text  # la captura del error sigue enlazada
    tarda, r = _segundos(cliente_grande, "/reels")
    assert tarda < 1.5, f"Reels tardó {tarda:.1f} s"


def test_las_consultas_del_registro_no_recorren_envios_ni_comentarios_enteros(conn):
    """Lo que hace rápido al panel son los índices: que no se pierdan en una migración."""
    plan = lambda sql: " | ".join(f[3] for f in conn.execute("EXPLAIN QUERY PLAN " + sql))  # noqa: E731
    assert "SCAN" not in plan("SELECT 1 FROM envios m WHERE m.user_id = 'u1' AND m.tipo = 'dm_manual' ORDER BY m.id DESC LIMIT 1")
    assert "SCAN" not in plan("SELECT COUNT(*) FROM comentarios c WHERE c.reel_id = 'r1'")
    assert "SCAN" not in plan("SELECT 1 FROM comentarios WHERE user_id = 'u1' AND reel_id = 'r1' LIMIT 1")
    assert "TEMP B-TREE" not in plan("SELECT id FROM comentarios ORDER BY comentado_en DESC LIMIT 500")
    assert "SCAN" not in plan("SELECT 1 FROM envios WHERE comentario_id = 'c1' AND tipo = 'publica_manual' ORDER BY id DESC LIMIT 1")
