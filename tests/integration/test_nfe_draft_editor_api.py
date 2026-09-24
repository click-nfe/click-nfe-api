from datetime import datetime, timedelta

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import Client, ImportProcess, NfeDraft, NfeDraftItem, Organization, User


class TestConfig:
    TESTING = True
    SECRET_KEY = "draft-editor-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


@pytest.fixture
def api():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization = Organization(nome="Organização Teste", slug="org-editor")
        db.session.add(organization)
        db.session.flush()
        user = User(
            organization_id=organization.id,
            nome="Fiscal Teste",
            email="fiscal-editor@example.invalid",
            role="admin",
            ativo=True,
        )
        user.set_password("test-password")
        importer = Client(
            organization_id=organization.id,
            cnpj="00000000000191",
            razao_social="Importadora Teste Ltda",
            ativo=True,
        )
        db.session.add_all([user, importer])
        db.session.flush()
        process = ImportProcess(
            organization_id=organization.id,
            importer_id=importer.id,
            reference_code="EDITOR-001",
            status="draft_ready",
            source="manual",
            created_by_user_id=user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(process)
        db.session.flush()

        def make_draft(*, number=None, status="ready_for_xml"):
            item_payload = {
                "item_number": 1,
                "duimp_item_number": "1",
                "product_code": "ITEM-1",
                "description": "Mercadoria para teste do editor",
                "ncm": "85044010",
                "cfop": "3102",
                "commercial_unit": "UN",
                "commercial_quantity": "1.0000",
                "commercial_unit_value": "100.0000000000",
                "taxable_unit": "UN",
                "taxable_quantity": "1.0000",
                "taxable_unit_value": "100.0000000000",
                "product_value": "100.00",
                "customs_value": "100.00",
                "net_weight": "10.00",
                "freight_value": "0.00",
                "insurance_value": "0.00",
                "discount_value": "0.00",
                "other_value": "0.00",
                "cost_allocation": {},
                "import_payload": {"addition_number": "1"},
                "tax_payload": {
                    "icms": {
                        "origin": "1",
                        "cst": "90",
                        "base_method": "3",
                        "base": "113.64",
                        "rate": "12.0000",
                        "value": "13.64",
                        "duimp_value": "13.64",
                        "calculation_source": "tax_rule",
                    },
                    "ii": {"base": "100.00", "value": "0.00", "customs_expenses": "0.00", "iof": "0.00"},
                    "ipi": {"base": "100.00", "rate": "0.00", "value": "0.00", "cst": "01"},
                    "pis": {"base": "100.00", "rate": "0.00", "value": "0.00", "cst": "98"},
                    "cofins": {"base": "100.00", "rate": "0.00", "value": "0.00", "cst": "98"},
                },
            }
            draft = NfeDraft(
                organization_id=organization.id,
                import_process_id=process.id,
                importer_id=importer.id,
                model="55",
                purpose="normal",
                operation_type="entry",
                environment="production",
                series="1",
                number=number,
                status=status,
                fiscal_payload={
                    "document": {"model": "55", "operation_type": "entry", "environment": "production", "series": "1"},
                    "duimp": {"number": "26BR0000000000-1"},
                    "items": [item_payload],
                    "additional_costs": {"afrmm": "0.00", "siscomex_fee": "0.00", "thc": "0.00", "other": "0.00"},
                    "reconciliation": {"status": "balanced", "checks": []},
                },
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.session.add(draft)
            db.session.flush()
            item = NfeDraftItem(
                nfe_draft_id=draft.id,
                item_number=1,
                duimp_item_number="1",
                product_code="ITEM-1",
                description="Mercadoria para teste do editor",
                ncm="85044010",
                cfop="3102",
                commercial_unit="UN",
                commercial_quantity="1",
                commercial_unit_value="100",
                taxable_unit="UN",
                taxable_quantity="1",
                taxable_unit_value="100",
                product_value="100",
                freight_value="0",
                insurance_value="0",
                discount_value="0",
                other_value="0",
                import_payload={"addition_number": "1", "cost_allocation": {}},
                tax_payload=item_payload["tax_payload"],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.session.add(item)
            db.session.flush()
            return draft, item

        editable, item = make_draft()
        reserved, _ = make_draft(number=77)
        signed, _ = make_draft(number=78, status="signed")
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
            str(process.id),
            str(editable.id),
            str(item.id),
            str(reserved.id),
            str(signed.id),
        )
        db.session.remove()
        db.drop_all()


def test_manual_tax_adjustment_is_recalculated_and_audited(api):
    client, headers, _, draft_id, item_id, _, _ = api
    response = client.patch(
        f"/nfe-drafts/{draft_id}/items/{item_id}/tax-adjustment",
        headers=headers,
        json={
            "source": "manual_adjustment",
            "reason": "Correção conferida pela equipe fiscal no processo.",
            "cfop": "3101",
            "icms": {
                "cst": "90",
                "base": "120.00",
                "rate": "12.00",
                "reduction_rate": "0",
            },
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    icms = body["item"]["tax_payload"]["icms"]
    assert body["item"]["cfop"] == "3101"
    assert icms["value"] == "14.40"
    assert icms["difference"] == "0.76"
    assert icms["calculation_source"] == "manual_adjustment"
    assert body["audit"]["changed_by_name"] == "Fiscal Teste"

    detail = client.get(f"/nfe-drafts/{draft_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.get_json()["auditTrail"][0]["reason"].startswith("Correção")


def test_additional_costs_reallocate_and_refresh_reconciliation(api):
    client, headers, _, draft_id, _, _, _ = api
    response = client.patch(
        f"/nfe-drafts/{draft_id}",
        headers=headers,
        json={
            "additional_costs": {
                "afrmm": "10.00",
                "siscomex_fee": "5.00",
                "thc": "20.00",
                "other": "2.23",
            }
        },
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()["draft"]["fiscal_payload"]
    assert payload["totals"]["afrmm_value"] == "10.00"
    assert payload["totals"]["thc_value"] == "20.00"
    assert payload["reconciliation"]["status"] == "balanced"
    assert payload["audit_trail"][-1]["section"] == "additional_costs"


def test_draft_editor_rejects_zero_bacen_country_code(api):
    client, headers, _, draft_id, _, _, _ = api
    response = client.patch(
        f"/nfe-drafts/{draft_id}",
        headers=headers,
        json={
            "foreign_supplier": {
                "country_code": "0000",
                "country_name": "China, República Popular",
            }
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
    assert "country_code" in response.get_json()["messages"]["foreign_supplier"]


def test_draft_removal_is_logical_and_reserved_number_is_archived(api):
    client, headers, process_id, draft_id, _, reserved_id, _ = api
    deleted = client.delete(
        f"/nfe-drafts/{draft_id}",
        headers=headers,
        json={"reason": "Rascunho duplicado criado durante a conferência."},
    )
    assert deleted.status_code == 200
    assert deleted.get_json()["deletion_mode"] == "deleted"
    assert deleted.get_json()["requires_inutilization_review"] is False
    assert client.get(f"/nfe-drafts/{draft_id}", headers=headers).status_code == 404

    archived = client.post(
        f"/nfe-drafts/{reserved_id}/remove",
        headers=headers,
        json={"reason": "Numeração reservada incorretamente e deve ser revisada."},
    )
    assert archived.status_code == 200
    assert archived.get_json()["deletion_mode"] == "archived"
    assert archived.get_json()["requires_inutilization_review"] is True

    listed = client.get(f"/import-processes/{process_id}/nfe-drafts", headers=headers)
    removed = {row["id"]: row for row in listed.get_json()["items"]}
    assert removed[draft_id]["deletion_mode"] == "deleted"
    assert removed[reserved_id]["deletion_mode"] == "archived"


def test_signed_draft_cannot_be_removed(api):
    client, headers, _, _, _, _, signed_id = api
    response = client.delete(
        f"/nfe-drafts/{signed_id}",
        headers=headers,
        json={"reason": "Tentativa de remover documento já assinado fiscalmente."},
    )
    assert response.status_code == 400
    assert "assinados" in response.get_json()["message"]
