"""Middleware de seguridad para una instancia PÚBLICA (Railway/Render/etc.), ver
docs/despliegue_nube.md y sobreprecios/web/app.py `seguridad_publica`.

Dos controles independientes por variable de entorno:
  LIC_USUARIO + LIC_CLAVE -> HTTP Basic para toda la app.
  LIC_MODO_PUBLICO=1      -> de solo lectura (403 en cualquier escritura, /doc e /investigar
                             deshabilitados, cartel y CTA ocultos en el template).
Sin esas variables (caso local de siempre) el comportamiento no cambia.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from sobreprecios.web.app import app

client = TestClient(app, follow_redirects=False)


def _sin_vars(monkeypatch):
    for var in ("LIC_MODO_PUBLICO", "LIC_USUARIO", "LIC_CLAVE"):
        monkeypatch.delenv(var, raising=False)


def test_sin_variables_el_comportamiento_local_no_cambia(monkeypatch, tmp_path):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "solo lectura" not in resp.text.lower()
    assert "＋ Investigar una licitación" in resp.text
    # Una escritura normal (sin modo público ni auth) sigue redirigiendo 303, nunca 403/401.
    post = client.post("/licitacion/999999/validar", data={"nota": "x"})
    assert post.status_code == 303


def test_modo_publico_bloquea_cualquier_escritura(monkeypatch, tmp_path):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")

    get_ok = client.get("/")
    assert get_ok.status_code == 200

    post = client.post("/licitacion/999999/validar", data={"nota": "x"})
    assert post.status_code == 403
    assert "solo lectura" in post.json()["error"].lower()


def test_modo_publico_muestra_cartel_y_oculta_el_cta_de_investigar(monkeypatch, tmp_path):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")

    resp = client.get("/")
    assert resp.status_code == 200
    assert "versión pública de solo lectura" in resp.text.lower()
    assert "＋ Investigar una licitación" not in resp.text


def test_modo_publico_deshabilita_doc_e_investigar(monkeypatch, tmp_path):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")

    doc = client.get("/doc", params={"url": "https://comprar.gob.ar/x"})
    assert doc.status_code == 303 and doc.headers["location"].startswith("/?message=")

    investigar_page = client.get("/investigar")
    assert investigar_page.status_code == 303

    investigar_post = client.post("/investigar", data={"entrada": "algo"})
    assert investigar_post.status_code == 403   # POST: cae primero por "cualquier escritura"


def test_auth_basica_exige_credenciales_correctas(monkeypatch, tmp_path):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LIC_USUARIO", "demo")
    monkeypatch.setenv("LIC_CLAVE", "secreta123")

    sin_credenciales = client.get("/")
    assert sin_credenciales.status_code == 401
    assert sin_credenciales.headers["www-authenticate"].startswith("Basic")

    credenciales_malas = client.get("/", auth=("demo", "noesesta"))
    assert credenciales_malas.status_code == 401

    usuario_malo = client.get("/", auth=("otro", "secreta123"))
    assert usuario_malo.status_code == 401

    credenciales_ok = client.get("/", auth=("demo", "secreta123"))
    assert credenciales_ok.status_code == 200


def test_healthz_no_exige_credenciales_ni_se_bloquea_en_modo_publico(monkeypatch, tmp_path):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LIC_USUARIO", "demo")
    monkeypatch.setenv("LIC_CLAVE", "secreta123")
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_la_clave_nunca_aparece_en_los_logs(monkeypatch, tmp_path, caplog):
    _sin_vars(monkeypatch)
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("LIC_USUARIO", "demo")
    monkeypatch.setenv("LIC_CLAVE", "secreta123")

    with caplog.at_level("DEBUG"):
        client.get("/", auth=("demo", "secreta123"))
        client.get("/", auth=("demo", "credencial-mala"))
    assert "secreta123" not in caplog.text
    assert "credencial-mala" not in caplog.text
