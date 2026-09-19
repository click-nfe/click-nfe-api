from datetime import datetime, timedelta
import io

from cryptography.fernet import Fernet
import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import Client, ClientFiscalProfile, Organization, User
from tests.helpers import certificate_material


@pytest.fixture
def api(tmp_path):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "certificate-upload-test-secret"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        JWT_ACCESS_EXPIRES_SECONDS = 3600
        JWT_REFRESH_EXPIRES_SECONDS = 604800
        NFE_CERTIFICATE_STORAGE_PROVIDER = "local_encrypted_file"
        NFE_LOCAL_CERTIFICATE_DIR = str(tmp_path / "certificates")
        NFE_LOCAL_CERTIFICATE_KEY = Fernet.generate_key().decode("ascii")
        NFE_CERTIFICATE_MAX_BYTES = 2 * 1024 * 1024

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization = Organization(
            nome="Organização Certificado",
            slug="org-certificado",
        )
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome="Administrador",
            email="certificado@example.invalid",
            role="admin",
            ativo=True,
        )
        user.set_password("test-password")
        client = Client(
            organization_id=organization.id,
            cnpj="00000000000191",
            razao_social="Emitente Certificado Ltda",
            ativo=True,
        )
        db.session.add_all([user, client])
        db.session.flush()
        db.session.add(
            ClientFiscalProfile(
                organization_id=organization.id,
                client_id=client.id,
                legal_name=client.razao_social,
                cnpj=client.cnpj,
                tax_regime="3",
                street="Rua Teste",
                number="100",
                district="Centro",
                city_code="4106902",
                city_name="Curitiba",
                state="PR",
                zip_code="80000000",
                country_code="1058",
                country_name="Brasil",
                is_default=True,
            )
        )
        db.session.commit()

        now = datetime.utcnow()
        token = jwt.encode(
            {
                "sub": str(user.id),
                "email": user.email,
                "role": user.role,
                "principal_type": "user",
                "type": "access",
                "iat": now,
                "exp": now + timedelta(hours=1),
            },
            app.config["SECRET_KEY"],
            algorithm="HS256",
        )
        yield (
            app.test_client(),
            {"Authorization": f"Bearer {token}"},
            str(client.id),
            tmp_path / "certificates",
        )
        db.session.remove()
        db.drop_all()


def _upload(
    client,
    headers,
    client_id,
    material,
    password=None,
    environment="production",
):
    return client.post(
        f"/clients/{client_id}/fiscal-certificates/upload",
        headers=headers,
        data={
            "environment": environment,
            "password": (password or material.password.decode("utf-8")),
            "certificate": (
                io.BytesIO(material.pkcs12_bytes),
                "emitente.pfx",
            ),
        },
        content_type="multipart/form-data",
    )


def test_upload_validates_encrypts_and_registers_a1(api):
    client, headers, client_id, certificate_dir = api
    material = certificate_material("00000000000191")

    response = _upload(client, headers, client_id, material)

    assert response.status_code == 201
    body = response.get_json()
    assert body["valid"] is True
    assert body["provider"] == "local_encrypted_file"
    assert body["status"] == "pending_validation"
    assert body["issuer_cnpj"] == "00000000000191"
    assert len(body["certificate_fingerprint_sha256"]) == 64
    assert "certificate_ref" not in body
    assert "password_ref" not in body
    encrypted_files = list(certificate_dir.rglob("*.enc"))
    assert len(encrypted_files) == 2
    assert all(material.password not in path.read_bytes() for path in encrypted_files)

    listed = client.get(
        f"/clients/{client_id}/fiscal-certificates",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.get_json()[0]["id"] == body["id"]

    activated = client.post(
        (
            f"/clients/{client_id}/fiscal-certificates/"
            f"{body['id']}/activate"
        ),
        headers=headers,
        json={},
    )
    assert activated.status_code == 200
    assert activated.get_json()["status"] == "active"
    assert activated.get_json()["is_active"] is True


def test_upload_rejects_wrong_password_without_persisting_files(api):
    client, headers, client_id, certificate_dir = api
    material = certificate_material("00000000000191")

    response = _upload(
        client,
        headers,
        client_id,
        material,
        password="senha-incorreta",
    )

    assert response.status_code == 422
    assert "senha são inválidos" in response.get_json()["message"]
    assert list(certificate_dir.rglob("*.enc")) == []


def test_upload_rejects_certificate_from_another_cnpj(api):
    client, headers, client_id, certificate_dir = api
    material = certificate_material("11111111000191")

    response = _upload(client, headers, client_id, material)

    assert response.status_code == 422
    assert "não corresponde ao emitente" in response.get_json()["message"]
    assert list(certificate_dir.rglob("*.enc")) == []


def test_upload_rejects_duplicate_without_creating_new_files(api):
    client, headers, client_id, certificate_dir = api
    material = certificate_material("00000000000191")

    assert _upload(client, headers, client_id, material).status_code == 201
    response = _upload(client, headers, client_id, material)

    assert response.status_code == 422
    assert "já está cadastrado" in response.get_json()["message"]
    assert len(list(certificate_dir.rglob("*.enc"))) == 2


def test_same_certificate_can_be_used_in_both_environments(api):
    client, headers, client_id, certificate_dir = api
    material = certificate_material("00000000000191")

    production = _upload(client, headers, client_id, material)
    homologation = _upload(
        client,
        headers,
        client_id,
        material,
        environment="homologation",
    )

    assert production.status_code == 201
    assert homologation.status_code == 201
    assert homologation.get_json()["environment"] == "homologation"
    assert len(list(certificate_dir.rglob("*.enc"))) == 4


def test_upload_removes_encrypted_files_when_database_commit_fails(
    api,
    monkeypatch,
):
    client, headers, client_id, certificate_dir = api
    material = certificate_material("00000000000191")

    def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db.session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="database unavailable"):
        _upload(client, headers, client_id, material)

    assert list(certificate_dir.rglob("*.enc")) == []
