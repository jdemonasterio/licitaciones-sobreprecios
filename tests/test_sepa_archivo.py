from sobreprecios.sources.sepa_archivo import BANDERAS_MAYORISTAS, fechas_mas_cercanas, parse_index

INDEX = """# index
* lunes, 19/08/2024:
  * abc  [✅ descargar](https://f004.backblazeb2.com/file/b/a-sepa_lunes.zip-repackaged.tar.zst) (primera vez visto)
* viernes, 23/08/2024:
  * x  ❌ (primera vez visto)
  * y  [✅ descargar](https://f004.backblazeb2.com/file/b/y-sepa_viernes.zip-repackaged.tar.zst) (visto)
  * z  [✅ descargar](https://f004.backblazeb2.com/file/b/z-sepa_viernes.zip-repackaged.tar.zst) (visto)
* sábado, 24/08/2024:
  * w  ❌ (primera vez visto)
"""


def test_parse_index_keeps_first_downloadable_per_day():
    idx = parse_index(INDEX)
    assert list(idx) == ["2024-08-19", "2024-08-23"]
    assert idx["2024-08-23"].endswith("y-sepa_viernes.zip-repackaged.tar.zst")


def test_fechas_mas_cercanas_dedupes_weeks_and_respects_window():
    disp = ["2024-08-19", "2024-08-23", "2024-09-30"]
    assert fechas_mas_cercanas(disp, ["2024-08-20", "2024-08-22", "2024-12-01"], max_dias=15) == ["2024-08-19"]


def test_mayorista_banners():
    assert {"maxi", "maxi comodin"} <= BANDERAS_MAYORISTAS
