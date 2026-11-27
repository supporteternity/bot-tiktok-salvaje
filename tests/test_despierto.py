import sys

import pytest

from bot.despierto import Despierto


@pytest.mark.skipif(sys.platform != "darwin", reason="caffeinate solo en Mac (Windows se prueba en G1)")
def test_caffeinate_arranca_y_se_para():
    with Despierto() as d:
        assert d._proceso is not None and d._proceso.poll() is None
        proceso = d._proceso
    assert proceso.poll() is not None
