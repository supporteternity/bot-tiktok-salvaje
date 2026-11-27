from datetime import UTC, datetime, timedelta

from bot import redaccion as r

AHORA = datetime(2026, 11, 24, 18, 0, tzinfo=UTC)  # 11:00 a. m. en Utah (UTC-7)


def test_horas_en_lenguaje_normal_y_hora_de_utah():
    assert r.hora(AHORA) == "11:00 a. m."
    assert r.cuando(AHORA + timedelta(hours=8, minutes=6), AHORA) == "hoy a las 7:06 p. m. (hora de Utah)"
    assert r.cuando(AHORA + timedelta(days=1), AHORA) == "mañana a las 11:00 a. m. (hora de Utah)"
    assert r.cuando(AHORA + timedelta(days=2), AHORA) == "el jueves 26 de noviembre a las 11:00 a. m. (hora de Utah)"


def test_duraciones_plurales_y_asunto():
    assert [r.duracion(s) for s in (30, 20 * 60, 90 * 60, 5 * 3600, 3 * 86400)] == ["menos de 1 min", "20 min", "1.5 h", "5 h", "3 días"]
    assert r.plural(1, "persona", "personas") == "1 persona" and r.plural(3, "persona", "personas") == "3 personas"
    assert r.asunto(r.TE_NECESITA, "Te necesita: algo") == "🔴 Bot de TikTok · Te necesita: algo"
    assert r.tecnico("") == "" and "para soporte" in r.tecnico("x")
