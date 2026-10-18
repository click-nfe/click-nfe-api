from types import SimpleNamespace

import pytest

from app.integrations.portal_unico.client import (
    GcpManagedPortalCredentialStore,
    PortalUnicoCredentials,
    PortalUnicoIntegrationError,
)
from app.services.fiscal_certificate import FiscalCertificateError
from app.services.gcp_fiscal_storage import GcpCloudStorageCertificateVault
from tests.helpers import certificate_material


class SecretClient:
    def __init__(self):
        self.versions = {}
        self.destroyed = []
        self.fail_add = False

    def add_secret_version(self, *, request):
        if self.fail_add:
            raise RuntimeError("sensitive provider response")
        parent = request["parent"]
        version = sum(key.startswith(parent + "/versions/") for key in self.versions) + 1
        name = f"{parent}/versions/{version}"
        self.versions[name] = request["payload"]["data"]
        return SimpleNamespace(name=name)

    def access_secret_version(self, *, request):
        return SimpleNamespace(payload=SimpleNamespace(data=self.versions[request["name"]]))

    def destroy_secret_version(self, *, request):
        self.destroyed.append(request["name"])
        self.versions.pop(request["name"])


class Blob:
    def __init__(self, blobs, key, generation=None):
        self.blobs = blobs
        self.key = key
        self.generation = generation

    def upload_from_string(self, content, *, content_type, if_generation_match):
        assert content_type == "application/x-pkcs12"
        assert if_generation_match == 0
        assert self.key not in self.blobs
        self.generation = 1
        self.blobs[self.key] = content

    def download_as_bytes(self):
        return self.blobs[self.key]

    def delete(self, *, if_generation_match):
        assert if_generation_match == 1
        del self.blobs[self.key]


class StorageClient:
    def __init__(self):
        self.blobs = {}

    def bucket(self, name):
        assert name == "click-nfe-test-a1"
        return self

    def blob(self, key, generation=None):
        return Blob(self.blobs, key, generation)


ORG = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"


def certificate_vault(secrets, storage):
    return GcpCloudStorageCertificateVault(
        bucket_name="click-nfe-test-a1", project_id="project-test",
        storage_client=storage, secret_client=secrets,
    )


def test_certificate_upload_resolve_and_delete_are_version_pinned():
    secrets, storage = SecretClient(), StorageClient()
    vault = certificate_vault(secrets, storage)
    material = certificate_material("00000000000191")
    refs = vault.store(organization_id=ORG, client_id=CLIENT, material=material)

    assert refs.provider == "gcp_cloud_storage"
    assert refs.certificate_ref.startswith(f"gcs:click-nfe-test-a1/{ORG}/{CLIENT}/")
    assert refs.password_ref == "gcp:click-nfe-a1-passwords@1"
    assert vault.resolve(provider=refs.provider, certificate_ref=refs.certificate_ref,
                         password_ref=refs.password_ref) == material

    vault.delete(refs)
    assert not secrets.versions
    assert not storage.blobs


def test_certificate_upload_cleans_object_when_secret_write_fails():
    secrets, storage = SecretClient(), StorageClient()
    secrets.fail_add = True
    vault = certificate_vault(secrets, storage)

    with pytest.raises(FiscalCertificateError) as exc:
        vault.store(organization_id=ORG, client_id=CLIENT,
                    material=certificate_material("00000000000191"))
    assert "sensitive" not in str(exc.value)
    assert not storage.blobs


def test_certificate_rejects_mismatched_password_and_bucket():
    secrets, storage = SecretClient(), StorageClient()
    vault = certificate_vault(secrets, storage)
    material = certificate_material("00000000000191")
    first = vault.store(organization_id=ORG, client_id=CLIENT, material=material)
    second = vault.store(organization_id=ORG, client_id=CLIENT, material=material)
    with pytest.raises(FiscalCertificateError, match="recuperar"):
        vault.resolve(provider=first.provider, certificate_ref=first.certificate_ref,
                      password_ref=second.password_ref)
    with pytest.raises(FiscalCertificateError, match="Referência"):
        vault.resolve(provider=first.provider,
                      certificate_ref=first.certificate_ref.replace("click-nfe-test-a1", "foreign-bucket"),
                      password_ref=first.password_ref)


def test_portal_credentials_are_tenant_bound_and_rotate_atomically():
    secrets = SecretClient()
    store = GcpManagedPortalCredentialStore(
        project_id="project-test", secret_name="click-nfe-portal-unico-credentials", client=secrets
    )
    first = store.store(organization_id=ORG, credentials=PortalUnicoCredentials("id-1", "secret-1"))
    second = store.store(organization_id=ORG, credentials=PortalUnicoCredentials("id-2", "secret-2"))
    assert first == f"gcp-managed:{ORG}@1"
    assert second == f"gcp-managed:{ORG}@2"
    assert store.resolve(second, role_type="IMPEXP").client_secret == "secret-2"
    assert store.resolve(first, role_type="IMPEXP").client_secret == "secret-1"
    with pytest.raises(PortalUnicoIntegrationError, match="recuperar"):
        store.resolve(f"gcp-managed:{CLIENT}@2", role_type="IMPEXP")
    store.delete(second)
    assert store.resolve(first, role_type="IMPEXP").client_secret == "secret-1"


def test_portal_write_failure_hides_provider_details():
    secrets = SecretClient()
    secrets.fail_add = True
    store = GcpManagedPortalCredentialStore(
        project_id="project-test", secret_name="click-nfe-portal-unico-credentials", client=secrets
    )
    with pytest.raises(PortalUnicoIntegrationError) as exc:
        store.store(organization_id=ORG, credentials=PortalUnicoCredentials("id", "secret"))
    assert "sensitive" not in str(exc.value)
