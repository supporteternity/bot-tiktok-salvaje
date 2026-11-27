import logging

import pytest

from bot import config, db

TABLAS = {"reels", "contactos", "comentarios", "acciones", "eventos", "textos", "config", "estado_bot"}


@pytest.fixture
def datos(tmp_path, monkeypatch):
    carpeta = tmp_path / "datos con espacios"
    monkeypatch.setenv(config.VAR_CARPETA_DATOS, str(carpeta))
    return carpeta


def test_preparar_crea_base_con_todas_las_tablas_y_wal(datos):
    conn = db.preparar()
    assert (datos / "bot.db").is_file()
    tablas = {f["name"] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLAS <= tablas
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.VERSION_ESQUEMA


def test_config_por_defecto(datos):
    conn = db.preparar()
    assert config.leer(conn, "modo_publica") == "todos"  # D43
    assert config.leer(conn, "orden_cola") == "videos_recientes"  # D42
    assert config.leer(conn, "modo_prueba") is True
    assert config.leer(conn, "nivel_ritmo") == 3
    assert config.leer(conn, "descanso_cada") == [12, 20]
    assert config.leer(conn, "inicio_desde")  # fecha de instalación
    assert set(config.DEFAULTS) <= {f["clave"] for f in conn.execute("SELECT clave FROM config")}
    estado = conn.execute("SELECT * FROM estado_bot").fetchone()
    assert estado["id"] == 1 and estado["nivel_ritmo"] == 3


def test_preparar_dos_veces_no_pisa_nada(datos):
    conn = db.preparar()
    inicio = config.leer(conn, "inicio_desde")
    config.guardar(conn, "modo_publica", "todos")
    conn.close()
    conn = db.preparar()
    assert config.leer(conn, "inicio_desde") == inicio
    assert config.leer(conn, "modo_publica") == "todos"


def test_config_toml_da_valores_iniciales(datos):
    datos.mkdir(parents=True)
    (datos / "config.toml").write_text(
        'cuenta = "cuentaprueba"\nmodo_publica = "todos"\ncuentas_equipo = ["setter1"]\n', encoding="utf-8"
    )
    conn = db.preparar()
    assert config.leer(conn, "cuenta") == "cuentaprueba"
    assert config.leer(conn, "modo_publica") == "todos"
    assert config.leer(conn, "cuentas_equipo") == ["setter1"]


def test_config_toml_con_clave_desconocida_o_valor_invalido_falla(datos):
    datos.mkdir(parents=True)
    (datos / "config.toml").write_text('modo_publik = "todos"\n', encoding="utf-8")
    with pytest.raises(config.ErrorConfig, match="modo_publik"):
        db.preparar()
    (datos / "config.toml").write_text('modo_publica = "a_veces"\n', encoding="utf-8")
    with pytest.raises(config.ErrorConfig, match="modo_publica"):
        db.preparar()


def test_secretos_salen_del_env_y_nunca_van_a_la_base_ni_a_los_logs(datos, caplog):
    datos.mkdir(parents=True)
    (datos / ".env").write_text("SMTP_USUARIO=yo@ejemplo.com\nSMTP_CONTRASENA=supersecreta123\n", encoding="utf-8")
    caplog.set_level(logging.DEBUG)
    conn = db.preparar()
    secretos = config.cargar_secretos()
    assert secretos.get("SMTP_CONTRASENA") == "supersecreta123"
    assert "supersecreta123" not in repr(secretos) and "supersecreta123" not in str(secretos)
    logging.getLogger("bot").info("secretos: %s", secretos)
    assert "supersecreta123" not in caplog.text
    volcado = "\n".join(conn.iterdump())
    assert "supersecreta123" not in volcado
    with pytest.raises(config.ErrorConfig):
        config.guardar(conn, "SMTP_CONTRASENA", "x")


def test_tope_de_lecturas_antiguo_sube_a_18_y_uno_elegido_a_mano_se_respeta(tmp_path):
    from bot import config, db

    conn = db.conectar(tmp_path / "bot.db")
    db.migrar(conn)
    config.inicializar(conn, tmp_path)
    config.guardar(conn, "tope_lecturas_hora", 12)
    config.inicializar(conn, tmp_path)
    assert config.leer(conn, "tope_lecturas_hora") == 18
    config.guardar(conn, "tope_lecturas_hora", 10)
    config.inicializar(conn, tmp_path)
    assert config.leer(conn, "tope_lecturas_hora") == 10


def test_textos_del_instructivo_precargados_sin_pisar(datos):
    """D43: al crear la base vienen los textos del instructivo y el enlace de la academia; no se pisa lo escrito."""
    from bot import textos

    conn = db.preparar()
    por_tipo = {t: n for t, n in conn.execute("SELECT tipo, COUNT(*) FROM textos GROUP BY tipo")}
    assert por_tipo["respuesta"] == 9 and por_tipo["respuesta_alt"] == 4 and por_tipo["seguimiento"] == 2
    assert set(por_tipo) == set(textos.TEXTOS_INSTRUCTIVO)
    assert config.leer(conn, "landing") == textos.LANDING_INSTRUCTIVO
    with conn:
        conn.execute("DELETE FROM textos WHERE tipo = 'animo'")
        conn.execute("UPDATE textos SET contenido = 'mío' WHERE tipo = 'dm'")
    config.guardar(conn, "landing", "https://otra.com")
    conn.close()
    conn = db.preparar()
    assert conn.execute("SELECT COUNT(*) FROM textos WHERE tipo = 'animo'").fetchone()[0] == 1  # vacío → se vuelve a cargar
    assert conn.execute("SELECT contenido FROM textos WHERE tipo = 'dm'").fetchone()[0] == "mío"
    assert config.leer(conn, "landing") == "https://otra.com"


def test_migracion_v3_pone_etapa_b_a_quien_ya_tiene_la_info(datos):
    conn = db.conectar(config.carpeta_datos(crear=True) / db.NOMBRE_DB)
    conn.executescript("BEGIN;" + db.MIGRACIONES[0] + db.MIGRACIONES[1] + "PRAGMA user_version = 2; COMMIT;")
    with conn:
        conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado) VALUES ('1', 'a', 1, 'dm_enviado')")
        conn.execute("INSERT INTO contactos (user_id, usuario, contactado, resultado) VALUES ('2', 'b', 1, 'dm_cerrado')")
        conn.execute("INSERT INTO contactos (user_id, usuario) VALUES ('3', 'c')")
    db.migrar(conn)
    assert [f[0] for f in conn.execute("SELECT etapa FROM contactos ORDER BY user_id")] == ["B", "A", "A"]
