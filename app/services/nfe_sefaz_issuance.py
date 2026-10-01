"""Persist the transmission intent before I/O and reconcile uncertain outcomes by key."""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import update

from app.extensions import db
from app.models.import_process import ImportProcess, ImportProcessStatus, NfeDraft, NfeDraftStatus, NfeXmlType, NfeXmlVersion
from app.models.nfe_issuance import (
    NfeAttemptOperation, NfeAttemptStatus, NfeIssuance,
    NfeIssuanceAttempt, NfeIssuanceEvent, NfeProtocol, NfeProtocolType,
)
from app.services.fiscal_certificate import A1CertificateInspector
from app.services.nfe_sefaz import (
    SefazClient, SefazConfigurationError, SefazReply, SefazTransportError,
    authorized_xml, signed_identity,
)


class SefazIssuanceService:
    def __init__(self, draft_service, client: SefazClient, *, enabled: bool):
        self.drafts = draft_service
        self.client = client
        self.enabled = enabled

    def _issuance(self, draft):
        return NfeIssuance.query.filter_by(
            organization_id=draft.organization_id, nfe_draft_id=draft.id,
        ).first()

    def status(self, draft):
        issuance = self._issuance(draft)
        if not issuance:
            return {"status": "not_signed", "enabled": self.enabled}
        attempts = (NfeIssuanceAttempt.query.filter_by(nfe_issuance_id=issuance.id)
                    .order_by(NfeIssuanceAttempt.started_at.desc()).limit(12).all())
        authorized = NfeXmlVersion.query.filter_by(
            nfe_draft_id=draft.id, xml_type=NfeXmlType.AUTHORIZED.value,
        ).order_by(NfeXmlVersion.version_number.desc()).first()
        return {
            "id": str(issuance.id), "status": issuance.status,
            "enabled": self.enabled, "access_key": issuance.access_key,
            "receipt_number": issuance.receipt_number,
            "protocol_number": issuance.protocol_number,
            "rejection_code": issuance.rejection_code,
            "rejection_reason": issuance.rejection_reason,
            "last_error": issuance.last_error_message,
            "authorized_xml_version_id": str(authorized.id) if authorized else None,
            "attempts": [{
                "operation": str(getattr(a.operation, "value", a.operation)),
                "status": str(getattr(a.status, "value", a.status)),
                "response_code": a.response_code, "response_message": a.response_message,
                "started_at": a.started_at.isoformat(),
            } for a in attempts],
        }

    def _material(self, issuance):
        cert = issuance.certificate
        if not cert or cert.client_id != issuance.importer_id or cert.organization_id != issuance.organization_id:
            raise SefazConfigurationError("Certificado da emissão não está disponível.")
        provider = str(getattr(cert.provider, "value", cert.provider))
        return self.drafts.certificate_vault.resolve(
            provider=provider, certificate_ref=cert.certificate_ref,
            password_ref=cert.password_ref,
        )

    def _signed(self, draft, issuance):
        xml = NfeXmlVersion.query.filter_by(
            nfe_draft_id=draft.id, xml_type=NfeXmlType.SIGNED.value,
        ).order_by(NfeXmlVersion.version_number.desc()).first()
        if not xml or xml.xsd_valid is not True or xml.access_key != issuance.access_key:
            raise SefazConfigurationError("XML assinado e válido não encontrado para esta emissão.")
        key, state, _ = signed_identity(xml.xml_content)
        if key != issuance.access_key:
            raise SefazConfigurationError("A chave do XML assinado não corresponde à emissão.")
        return xml, key, state

    def _attempt(self, issuance, operation, *, xml=None):
        count = NfeIssuanceAttempt.query.filter_by(
            nfe_issuance_id=issuance.id, operation=operation,
        ).count()
        attempt = NfeIssuanceAttempt(
            nfe_issuance_id=issuance.id, operation=operation,
            attempt_number=count + 1, status=NfeAttemptStatus.STARTED.value,
            request_checksum=hashlib.sha256(xml.xml_content.encode()).hexdigest() if xml else None,
            started_at=datetime.utcnow(),
        )
        db.session.add(attempt)
        db.session.flush()
        return attempt

    def _transition(self, issuance, status, reason):
        if issuance.status == status:
            return
        previous = issuance.status
        issuance.status = status
        issuance.updated_at = datetime.utcnow()
        db.session.add(NfeIssuanceEvent(
            nfe_issuance_id=issuance.id, previous_status=previous,
            current_status=status, reason=reason,
            actor_user_id=self.drafts.user_id, created_at=datetime.utcnow(),
        ))

    def transmit(self, draft):
        if not self.enabled:
            raise SefazConfigurationError("Transmissão SEFAZ desabilitada. Configure NFE_SEFAZ_TRANSMISSION_ENABLED.")
        issuance = self._issuance(draft)
        if not issuance or issuance.status != "signed":
            raise SefazConfigurationError("A emissão não está assinada ou já teve uma tentativa de transmissão. Consulte a situação.")
        xml, key, state = self._signed(draft, issuance)
        self.drafts.ensure_authorization_ready(draft)
        self.drafts.xml_signer.verify(xml.xml_content, expected_cnpj=issuance.certificate.issuer_cnpj)
        if not self.drafts.xsd_validator.validate(xml.xml_content, allow_unsigned=False).is_valid:
            raise SefazConfigurationError("O XML assinado não passou na validação XSD atual.")
        material = self._material(issuance)
        A1CertificateInspector().load(material, expected_cnpj=issuance.certificate.issuer_cnpj)
        self.client.endpoint(state, "authorization")
        # Commit the intent before network I/O. A concurrent request cannot send again.
        changed = db.session.execute(update(NfeIssuance).where(
            NfeIssuance.id == issuance.id, NfeIssuance.status == "signed",
        ).values(status="submission_pending", lock_version=NfeIssuance.lock_version + 1,
                 updated_at=datetime.utcnow())).rowcount
        if changed != 1:
            db.session.rollback()
            raise SefazConfigurationError("Outra transmissão já foi iniciada. Consulte a situação.")
        self._attempt(issuance, NfeAttemptOperation.AUTHORIZATION.value, xml=xml)
        db.session.add(NfeIssuanceEvent(
            nfe_issuance_id=issuance.id, previous_status="signed",
            current_status="submission_pending", reason="Envio SEFAZ iniciado.",
            actor_user_id=self.drafts.user_id, created_at=datetime.utcnow(),
        ))
        db.session.commit()
        try:
            reply = self.client.authorize(xml.xml_content, material)
        except Exception as exc:
            self._uncertain(issuance.id, NfeAttemptOperation.AUTHORIZATION.value)
            if isinstance(exc, SefazTransportError):
                raise
            raise SefazTransportError("Resultado incerto; consulte a chave antes de qualquer reenvio.") from exc
        self._finish(issuance.id, draft.id, xml, reply, NfeAttemptOperation.AUTHORIZATION.value)
        return self.status(draft)

    def reconcile(self, draft):
        if not self.enabled:
            raise SefazConfigurationError("Consultas SEFAZ desabilitadas.")
        issuance = self._issuance(draft)
        if not issuance or issuance.status not in {"submission_pending", "submitted", "processing"}:
            raise SefazConfigurationError("Não há envio pendente para consulta.")
        xml, key, state = self._signed(draft, issuance)
        operation = (NfeAttemptOperation.RECEIPT_QUERY.value if issuance.receipt_number
                     else NfeAttemptOperation.PROTOCOL_QUERY.value)
        self.client.endpoint(state, "receipt" if issuance.receipt_number else "protocol")
        material = self._material(issuance)
        attempt = self._attempt(issuance, operation)
        db.session.commit()
        try:
            if issuance.receipt_number:
                reply = self.client.query_receipt(key, state, issuance.receipt_number, material)
            else:
                reply = self.client.query_protocol(key, state, material)
        except Exception as exc:
            self._uncertain(issuance.id, operation)
            if isinstance(exc, SefazTransportError):
                raise
            raise SefazTransportError("Consulta inconclusiva; repita a consulta pela chave.") from exc
        self._finish(issuance.id, draft.id, xml, reply, operation)
        return self.status(draft)

    def _uncertain(self, issuance_id, operation):
        issuance = db.session.get(NfeIssuance, issuance_id)
        attempt = NfeIssuanceAttempt.query.filter_by(
            nfe_issuance_id=issuance_id, operation=operation,
        ).order_by(NfeIssuanceAttempt.attempt_number.desc()).first()
        attempt.status = NfeAttemptStatus.UNKNOWN.value
        attempt.error_message = "Resultado de comunicação incerto; consultar SEFAZ."
        attempt.finished_at = datetime.utcnow()
        issuance.last_error_message = attempt.error_message
        self._transition(issuance, "processing", "Resposta incerta; requer consulta.")
        process = db.session.get(ImportProcess, issuance.import_process_id)
        if process:
            process.status, process.updated_at = ImportProcessStatus.TRANSMISSION_PENDING.value, datetime.utcnow()
        db.session.commit()

    def _finish(self, issuance_id, draft_id, xml, reply: SefazReply, operation):
        issuance = db.session.get(NfeIssuance, issuance_id)
        draft = self.drafts.nfe_draft_query_for_current_user().filter_by(id=draft_id).first()
        attempt = NfeIssuanceAttempt.query.filter_by(
            nfe_issuance_id=issuance_id, operation=operation,
        ).order_by(NfeIssuanceAttempt.attempt_number.desc()).first()
        attempt.status = NfeAttemptStatus.SUCCEEDED.value
        attempt.response_code, attempt.response_message = reply.code, reply.message
        attempt.response_checksum, attempt.finished_at = reply.checksum, datetime.utcnow()
        if reply.receipt:
            issuance.receipt_number = reply.receipt
            attempt.receipt_number = reply.receipt
        issuance.last_error_message = None
        if reply.code in {"100", "150"}:
            content = authorized_xml(xml.xml_content, reply)
            if not NfeXmlVersion.query.filter_by(nfe_draft_id=draft.id, xml_type=NfeXmlType.AUTHORIZED.value).first():
                db.session.add(NfeXmlVersion(
                    nfe_draft_id=draft.id, version_number=xml.version_number,
                    xml_type=NfeXmlType.AUTHORIZED.value, xml_content=content,
                    xsd_valid=True, xsd_errors=[], access_key=issuance.access_key,
                    protocol_number=reply.protocol, generated_at=datetime.utcnow(),
                    generated_by_user_id=self.drafts.user_id,
                ))
                db.session.add(NfeProtocol(
                    nfe_issuance_id=issuance.id, protocol_type=NfeProtocolType.AUTHORIZATION.value,
                    event_sequence=1, status_code=reply.code, status_message=reply.message,
                    protocol_number=reply.protocol, response_xml=reply.protocol_xml,
                    response_checksum_sha256=hashlib.sha256(reply.protocol_xml.encode()).hexdigest(),
                    received_at=datetime.utcnow(), created_at=datetime.utcnow(),
                ))
            issuance.protocol_number = reply.protocol
            attempt.protocol_number = reply.protocol
            issuance.authorized_at = datetime.utcnow()
            self._transition(issuance, "authorized", "Protocolo de autorização recebido.")
            draft.status = NfeDraftStatus.AUTHORIZED.value
            process = db.session.get(ImportProcess, draft.import_process_id)
            if process:
                children = NfeDraft.query.filter_by(import_process_id=process.id, deleted_at=None).filter(NfeDraft.planned_document_id.isnot(None)).all()
                process.status = (ImportProcessStatus.AUTHORIZED.value if children and all(
                    str(getattr(child.status, "value", child.status)) == NfeDraftStatus.AUTHORIZED.value
                    for child in children
                ) else ImportProcessStatus.TRANSMITTED.value)
                process.updated_at = datetime.utcnow()
        elif reply.code in {"103", "104", "105", "204", "217", "108", "109"}:
            self._transition(issuance, "processing", f"Consulta pendente ({reply.code}).")
            process = db.session.get(ImportProcess, draft.import_process_id)
            if process:
                process.status, process.updated_at = ImportProcessStatus.TRANSMISSION_PENDING.value, datetime.utcnow()
        elif reply.code in {"110", "301", "302"} and reply.protocol_xml:
            issuance.rejection_code, issuance.rejection_reason = reply.code, reply.message
            self._transition(issuance, "denied", f"Uso denegado pela SEFAZ ({reply.code}).")
            draft.status = NfeDraftStatus.REJECTED.value
        elif operation == NfeAttemptOperation.PROTOCOL_QUERY.value:
            # A key query without a protocol never proves that an earlier POST was not processed.
            self._transition(issuance, "processing", f"Consulta não conclusiva ({reply.code}).")
        else:
            issuance.rejection_code, issuance.rejection_reason = reply.code, reply.message
            self._transition(issuance, "rejected", f"SEFAZ rejeitou a NF-e ({reply.code}).")
            draft.status = NfeDraftStatus.REJECTED.value
            process = db.session.get(ImportProcess, draft.import_process_id)
            if process:
                process.status, process.updated_at = ImportProcessStatus.REJECTED.value, datetime.utcnow()
        db.session.commit()
