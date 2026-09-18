from cryptography.fernet import Fernet
import pytest

from app.services.fiscal_certificate import (
    FiscalCertificateError,
    LocalEncryptedFileCertificateVault,
    certificate_vault_from_config,
)
from tests.helpers import certificate_material


def test_local_vault_encrypts_and_resolves_certificate_material(tmp_path):
    material = certificate_material("00000000000191")
    vault = LocalEncryptedFileCertificateVault(
        root_dir=tmp_path,
        encryption_key=Fernet.generate_key(),
    )

    references = vault.store(
        organization_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        material=material,
    )
    encrypted_files = list(tmp_path.rglob("*.enc"))

    assert len(encrypted_files) == 2
    assert all(material.pkcs12_bytes not in path.read_bytes() for path in encrypted_files)
    assert all(material.password not in path.read_bytes() for path in encrypted_files)
    resolved = vault.resolve(
        provider=references.provider,
        certificate_ref=references.certificate_ref,
        password_ref=references.password_ref,
    )
    assert resolved == material


def test_local_vault_rejects_reference_outside_managed_directory(tmp_path):
    vault = LocalEncryptedFileCertificateVault(
        root_dir=tmp_path,
        encryption_key=Fernet.generate_key(),
    )

    with pytest.raises(FiscalCertificateError, match="referência local"):
        vault.resolve(
            provider="local_encrypted_file",
            certificate_ref="local:../../arquivo.pfx",
            password_ref="local:../../senha.txt",
        )


def test_local_vault_removes_both_files_on_delete(tmp_path):
    vault = LocalEncryptedFileCertificateVault(
        root_dir=tmp_path,
        encryption_key=Fernet.generate_key(),
    )
    references = vault.store(
        organization_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        material=certificate_material("00000000000191"),
    )

    vault.delete(references)

    assert list(tmp_path.rglob("*.enc")) == []


def test_configured_vault_resolves_material_with_flask_config_key(tmp_path):
    material = certificate_material("00000000000191")
    encryption_key = Fernet.generate_key().decode("ascii")
    local_vault = LocalEncryptedFileCertificateVault(
        root_dir=tmp_path,
        encryption_key=encryption_key,
    )
    references = local_vault.store(
        organization_id="11111111-1111-1111-1111-111111111111",
        client_id="22222222-2222-2222-2222-222222222222",
        material=material,
    )

    vault = certificate_vault_from_config(
        {
            "NFE_LOCAL_CERTIFICATE_DIR": str(tmp_path),
            "NFE_LOCAL_CERTIFICATE_KEY": encryption_key,
            "SECRET_KEY": "not-used",
        }
    )

    assert vault.resolve(
        provider=references.provider,
        certificate_ref=references.certificate_ref,
        password_ref=references.password_ref,
    ) == material
