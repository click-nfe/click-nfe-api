from flask import Blueprint, g, jsonify, request
from marshmallow import ValidationError
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from uuid import UUID

from app.auth import admin_required
from app.extensions import db
from app.models import (
    Preposto,
    PrepostoContato,
    PrepostoCredenciado,
    PrepostoCredenciadoVinculo,
    PrepostoLocalidade,
    PrepostoTarifa,
)
from app.schemas import (
    PrepostoSchema,
    PrepostoCreateSchema,
    PrepostoUpdateSchema,
    PrepostoContatoSchema,
    PrepostoContatoCreateSchema,
    PrepostoContatoUpdateSchema,
    PrepostoLocalidadeSchema,
    PrepostoLocalidadeCreateSchema,
    PrepostoLocalidadeUpdateSchema,
    PrepostoLookupResponseSchema,
    PrepostoTarifaCreateSchema,
    PrepostoTarifaSchema,
    PrepostoTarifaUpdateSchema,
    PrepostoCredenciadoAdminSchema,
    PrepostoCredenciadoCreateSchema,
    PrepostoCredenciadoUpdateSchema,
    PrepostoCredenciadoVinculoCreateSchema,
)

prepostos_bp = Blueprint("prepostos", __name__, url_prefix="/prepostos")


def as_uuid(value):
    return value if isinstance(value, UUID) else UUID(str(value))

def json_error(message: str, status_code: int = 400, errors=None):
    payload = {"message": message}
    if errors is not None:
        payload["errors"] = errors
    return jsonify(payload), status_code


def get_preposto_or_404(preposto_id: str):
    preposto = Preposto.query.filter_by(
        id=as_uuid(preposto_id),
        organization_id=g.current_user.organization_id,
    ).first()
    if not preposto:
        return None
    return preposto


def get_contato_or_404(preposto_id: str, contato_id: str):
    contato = PrepostoContato.query.filter_by(
        id=as_uuid(contato_id),
        preposto_id=as_uuid(preposto_id),
    ).first()
    if not contato:
        return None
    return contato


def get_localidade_or_404(preposto_id: str, localidade_id: str):
    localidade = PrepostoLocalidade.query.filter_by(
        id=as_uuid(localidade_id),
        preposto_id=as_uuid(preposto_id),
    ).first()
    if not localidade:
        return None
    return localidade


def get_tarifa_or_404(localidade_id: str, tarifa_id: str):
    return PrepostoTarifa.query.filter_by(
        id=as_uuid(tarifa_id),
        localidade_id=as_uuid(localidade_id),
    ).first()


def get_credenciado_or_404(credenciado_id: str):
    return PrepostoCredenciado.query.filter_by(
        id=as_uuid(credenciado_id),
        organization_id=g.current_user.organization_id,
    ).first()


def clear_other_principais(preposto_id, contato_id=None):
    q = PrepostoContato.query.filter_by(preposto_id=preposto_id, principal=True)
    if contato_id:
        q = q.filter(PrepostoContato.id != contato_id)

    for contato in q.all():
        contato.principal = False


@prepostos_bp.post("")
@admin_required
def create_preposto():
    try:
        payload = PrepostoCreateSchema().load(request.get_json() or {})
    except ValidationError as err:
        return json_error("Dados inválidos para criação do preposto.", 422, err.messages)

    preposto = Preposto(
        organization_id=g.current_user.organization_id,
        nome=payload["nome"].strip(),
        razao_social=payload.get("razao_social"),
        ativo=payload.get("ativo", True),
        observacoes=payload.get("observacoes"),
    )

    db.session.add(preposto)
    db.session.commit()

    return jsonify(PrepostoSchema().dump(preposto)), 201


@prepostos_bp.get("")
@admin_required
def list_prepostos():
    search = (request.args.get("q") or request.args.get("nome") or "").strip()
    uf = request.args.get("uf", "").strip().upper()
    operacao = request.args.get("operacao", "").strip().upper()
    ativo = request.args.get("ativo")

    q = Preposto.query.filter_by(organization_id=g.current_user.organization_id)

    if search:
        pattern = f"%{search}%"
        q = (
            q.outerjoin(Preposto.contatos)
            .outerjoin(Preposto.localidades)
            .outerjoin(PrepostoLocalidade.tarifas)
            .outerjoin(Preposto.credenciado_links)
            .outerjoin(PrepostoCredenciadoVinculo.credenciado)
            .filter(
                or_(
                    Preposto.nome.ilike(pattern),
                    Preposto.razao_social.ilike(pattern),
                    PrepostoContato.nome.ilike(pattern),
                    PrepostoContato.email.ilike(pattern),
                    PrepostoContato.telefone.ilike(pattern),
                    PrepostoLocalidade.cidade.ilike(pattern),
                    PrepostoLocalidade.uf.ilike(pattern),
                    PrepostoLocalidade.descricao_local.ilike(pattern),
                    PrepostoTarifa.condicao.ilike(pattern),
                    PrepostoCredenciado.nome.ilike(pattern),
                    PrepostoCredenciado.registro_rfb.ilike(pattern),
                )
            )
            .distinct()
        )

    if uf:
        q = q.filter(Preposto.localidades.any(PrepostoLocalidade.uf == uf))

    if operacao == "IMPORTACAO":
        q = q.filter(
            Preposto.localidades.any(PrepostoLocalidade.atende_importacao.is_(True))
        )
    elif operacao == "EXPORTACAO":
        q = q.filter(
            Preposto.localidades.any(PrepostoLocalidade.atende_exportacao.is_(True))
        )
    elif operacao not in ("", "AMBAS"):
        return json_error(
            "Operação inválida. Utilize IMPORTACAO, EXPORTACAO ou AMBAS.",
            422,
        )

    if ativo is not None:
        ativo_bool = ativo.lower() in ("1", "true", "t", "sim", "yes")
        q = q.filter(Preposto.ativo.is_(ativo_bool))

    rows = q.order_by(Preposto.nome.asc()).all()

    locality_ids = [localidade.id for row in rows for localidade in row.localidades]
    credential_ids = {
        link.credenciado_id
        for row in rows
        for link in row.credenciado_links
        if link.ativo
    }

    return jsonify(
        {
            "items": PrepostoSchema(many=True).dump(rows),
            "total": len(rows),
            "summary": {
                "prepostos": len(rows),
                "localidades": len(locality_ids),
                "tarifas": PrepostoTarifa.query.filter(
                    PrepostoTarifa.localidade_id.in_(locality_ids)
                ).count()
                if locality_ids
                else 0,
                "credenciados": len(credential_ids),
            },
        }
    ), 200


@prepostos_bp.get("/<uuid:preposto_id>")
@admin_required
def get_preposto(preposto_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    return jsonify(PrepostoSchema().dump(preposto)), 200


@prepostos_bp.patch("/<uuid:preposto_id>")
@admin_required
def update_preposto(preposto_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    try:
        payload = PrepostoUpdateSchema().load(request.get_json() or {}, partial=True)
    except ValidationError as err:
        return json_error("Dados inválidos para atualização do preposto.", 422, err.messages)

    if "nome" in payload:
        preposto.nome = payload["nome"].strip()

    if "razao_social" in payload:
        preposto.razao_social = payload["razao_social"]

    if "ativo" in payload:
        preposto.ativo = payload["ativo"]

    if "observacoes" in payload:
        preposto.observacoes = payload["observacoes"]

    db.session.commit()

    return jsonify(PrepostoSchema().dump(preposto)), 200


@prepostos_bp.delete("/<uuid:preposto_id>")
@admin_required
def delete_preposto(preposto_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    db.session.delete(preposto)
    db.session.commit()

    return jsonify({"message": "Preposto excluído com sucesso."}), 200


@prepostos_bp.post("/<uuid:preposto_id>/contatos")
@admin_required
def create_preposto_contato(preposto_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    try:
        payload = PrepostoContatoCreateSchema().load(request.get_json() or {})
    except ValidationError as err:
        return json_error("Dados inválidos para criação do contato.", 422, err.messages)

    contato = PrepostoContato(
        preposto_id=preposto.id,
        nome=payload["nome"].strip(),
        email=payload.get("email"),
        telefone=payload.get("telefone"),
        whatsapp=payload.get("whatsapp"),
        principal=payload.get("principal", False),
    )

    db.session.add(contato)
    db.session.flush()

    if contato.principal:
        clear_other_principais(preposto.id, contato.id)

    db.session.commit()

    return jsonify(PrepostoContatoSchema().dump(contato)), 201


@prepostos_bp.patch("/<uuid:preposto_id>/contatos/<uuid:contato_id>")
@admin_required
def update_preposto_contato(preposto_id, contato_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    contato = get_contato_or_404(str(preposto_id), str(contato_id))
    if not contato:
        return json_error("Contato não encontrado para este preposto.", 404)

    try:
        payload = PrepostoContatoUpdateSchema().load(request.get_json() or {}, partial=True)
    except ValidationError as err:
        return json_error("Dados inválidos para atualização do contato.", 422, err.messages)

    if "nome" in payload:
        contato.nome = payload["nome"].strip()

    if "email" in payload:
        contato.email = payload["email"]

    if "telefone" in payload:
        contato.telefone = payload["telefone"]

    if "whatsapp" in payload:
        contato.whatsapp = payload["whatsapp"]

    if "principal" in payload:
        contato.principal = payload["principal"]
        if contato.principal:
            clear_other_principais(preposto.id, contato.id)

    db.session.commit()

    return jsonify(PrepostoContatoSchema().dump(contato)), 200


@prepostos_bp.delete("/<uuid:preposto_id>/contatos/<uuid:contato_id>")
@admin_required
def delete_preposto_contato(preposto_id, contato_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    contato = get_contato_or_404(str(preposto_id), str(contato_id))
    if not contato:
        return json_error("Contato não encontrado para este preposto.", 404)

    db.session.delete(contato)
    db.session.commit()

    return jsonify({"message": "Contato excluído com sucesso."}), 200


@prepostos_bp.post("/<uuid:preposto_id>/localidades")
@admin_required
def create_preposto_localidade(preposto_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    try:
        payload = PrepostoLocalidadeCreateSchema().load(request.get_json() or {})
    except ValidationError as err:
        return json_error("Dados inválidos para criação da localidade.", 422, err.messages)

    localidade = PrepostoLocalidade(
        preposto_id=preposto.id,
        cidade=payload["cidade"].strip(),
        uf=payload.get("uf"),
        descricao_local=payload.get("descricao_local"),
        tipo_local=payload.get("tipo_local"),
        atende_importacao=payload.get("atende_importacao", False),
        atende_exportacao=payload.get("atende_exportacao", False),
        valor_importacao=payload.get("valor_importacao"),
        valor_exportacao=payload.get("valor_exportacao"),
        valor_importacao_descricao=payload.get("valor_importacao_descricao"),
        valor_exportacao_descricao=payload.get("valor_exportacao_descricao"),
        moeda=payload.get("moeda", "BRL"),
        observacoes=payload.get("observacoes"),
    )

    db.session.add(localidade)
    db.session.commit()

    return jsonify(PrepostoLocalidadeSchema().dump(localidade)), 201


@prepostos_bp.patch("/<uuid:preposto_id>/localidades/<uuid:localidade_id>")
@admin_required
def update_preposto_localidade(preposto_id, localidade_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    try:
        payload = PrepostoLocalidadeUpdateSchema().load(request.get_json() or {}, partial=True)
    except ValidationError as err:
        return json_error("Dados inválidos para atualização da localidade.", 422, err.messages)

    for field in [
        "cidade",
        "uf",
        "descricao_local",
        "tipo_local",
        "atende_importacao",
        "atende_exportacao",
        "valor_importacao",
        "valor_exportacao",
        "valor_importacao_descricao",
        "valor_exportacao_descricao",
        "moeda",
        "observacoes",
    ]:
        if field in payload:
            setattr(localidade, field, payload[field].strip() if field == "cidade" and payload[field] else payload[field])

    if not localidade.atende_importacao and not localidade.atende_exportacao:
        return json_error(
            "A localidade deve atender importação e/ou exportação.",
            422,
        )

    db.session.commit()

    return jsonify(PrepostoLocalidadeSchema().dump(localidade)), 200


@prepostos_bp.delete("/<uuid:preposto_id>/localidades/<uuid:localidade_id>")
@admin_required
def delete_preposto_localidade(preposto_id, localidade_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    db.session.delete(localidade)
    db.session.commit()

    return jsonify({"message": "Localidade excluída com sucesso."}), 200


@prepostos_bp.post(
    "/<uuid:preposto_id>/localidades/<uuid:localidade_id>/tarifas"
)
@admin_required
def create_preposto_tarifa(preposto_id, localidade_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    try:
        payload = PrepostoTarifaCreateSchema().load(request.get_json() or {})
    except ValidationError as err:
        return json_error("Dados inválidos para criação da tarifa.", 422, err.messages)

    tarifa = PrepostoTarifa(localidade_id=localidade.id, **payload)
    if tarifa.principal:
        PrepostoTarifa.query.filter_by(
            localidade_id=localidade.id,
            operacao=tarifa.operacao,
            principal=True,
        ).update({"principal": False})

    db.session.add(tarifa)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return json_error(
            "Já existe uma tarifa com este código para a localidade.",
            409,
        )

    return jsonify(PrepostoTarifaSchema().dump(tarifa)), 201


@prepostos_bp.patch(
    "/<uuid:preposto_id>/localidades/<uuid:localidade_id>/tarifas/<uuid:tarifa_id>"
)
@admin_required
def update_preposto_tarifa(preposto_id, localidade_id, tarifa_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    tarifa = get_tarifa_or_404(str(localidade_id), str(tarifa_id))
    if not tarifa:
        return json_error("Tarifa não encontrada para esta localidade.", 404)

    try:
        payload = PrepostoTarifaUpdateSchema().load(
            request.get_json() or {}, partial=True
        )
    except ValidationError as err:
        return json_error("Dados inválidos para atualização da tarifa.", 422, err.messages)

    for field, value in payload.items():
        setattr(tarifa, field, value)

    if tarifa.principal:
        PrepostoTarifa.query.filter(
            PrepostoTarifa.localidade_id == localidade.id,
            PrepostoTarifa.operacao == tarifa.operacao,
            PrepostoTarifa.id != tarifa.id,
            PrepostoTarifa.principal.is_(True),
        ).update({"principal": False}, synchronize_session=False)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return json_error(
            "Já existe uma tarifa com este código para a localidade.",
            409,
        )

    return jsonify(PrepostoTarifaSchema().dump(tarifa)), 200


@prepostos_bp.delete(
    "/<uuid:preposto_id>/localidades/<uuid:localidade_id>/tarifas/<uuid:tarifa_id>"
)
@admin_required
def delete_preposto_tarifa(preposto_id, localidade_id, tarifa_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    tarifa = get_tarifa_or_404(str(localidade_id), str(tarifa_id))
    if not tarifa:
        return json_error("Tarifa não encontrada para esta localidade.", 404)

    db.session.delete(tarifa)
    db.session.commit()
    return jsonify({"message": "Tarifa excluída com sucesso."}), 200


@prepostos_bp.get("/credenciados")
@admin_required
def list_preposto_credenciados():
    search = request.args.get("q", "").strip()
    ativo = request.args.get("ativo")
    q = PrepostoCredenciado.query.filter_by(
        organization_id=g.current_user.organization_id
    )

    if search:
        pattern = f"%{search}%"
        q = q.filter(
            or_(
                PrepostoCredenciado.nome.ilike(pattern),
                PrepostoCredenciado.cpf.ilike(pattern),
                PrepostoCredenciado.registro_rfb.ilike(pattern),
            )
        )
    if ativo is not None:
        ativo_bool = ativo.lower() in ("1", "true", "t", "sim", "yes")
        q = q.filter(PrepostoCredenciado.ativo.is_(ativo_bool))

    rows = q.order_by(PrepostoCredenciado.nome.asc()).all()
    return jsonify(
        {
            "items": PrepostoCredenciadoAdminSchema(many=True).dump(rows),
            "total": len(rows),
        }
    ), 200


@prepostos_bp.post("/credenciados")
@admin_required
def create_preposto_credenciado():
    try:
        payload = PrepostoCredenciadoCreateSchema().load(request.get_json() or {})
    except ValidationError as err:
        return json_error(
            "Dados inválidos para criação do credenciado.", 422, err.messages
        )

    credenciado = PrepostoCredenciado(
        organization_id=g.current_user.organization_id,
        **payload,
    )
    db.session.add(credenciado)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return json_error(
            "Já existe um credenciado com este CPF na organização.",
            409,
        )

    return jsonify(PrepostoCredenciadoAdminSchema().dump(credenciado)), 201


@prepostos_bp.patch("/credenciados/<uuid:credenciado_id>")
@admin_required
def update_preposto_credenciado(credenciado_id):
    credenciado = get_credenciado_or_404(str(credenciado_id))
    if not credenciado:
        return json_error("Credenciado não encontrado.", 404)

    try:
        payload = PrepostoCredenciadoUpdateSchema().load(
            request.get_json() or {}, partial=True
        )
    except ValidationError as err:
        return json_error(
            "Dados inválidos para atualização do credenciado.", 422, err.messages
        )

    for field, value in payload.items():
        setattr(credenciado, field, value)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return json_error(
            "Já existe um credenciado com este CPF na organização.",
            409,
        )
    return jsonify(PrepostoCredenciadoAdminSchema().dump(credenciado)), 200


@prepostos_bp.delete("/credenciados/<uuid:credenciado_id>")
@admin_required
def delete_preposto_credenciado(credenciado_id):
    credenciado = get_credenciado_or_404(str(credenciado_id))
    if not credenciado:
        return json_error("Credenciado não encontrado.", 404)

    credenciado.ativo = False
    for link in credenciado.vinculos:
        link.ativo = False
    db.session.commit()
    return jsonify({"message": "Credenciado desativado com sucesso."}), 200


@prepostos_bp.post(
    "/<uuid:preposto_id>/localidades/<uuid:localidade_id>/credenciados"
)
@admin_required
def create_preposto_credenciado_vinculo(preposto_id, localidade_id):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    try:
        payload = PrepostoCredenciadoVinculoCreateSchema().load(
            request.get_json() or {}
        )
    except ValidationError as err:
        return json_error("Dados inválidos para criação do vínculo.", 422, err.messages)

    credenciado = get_credenciado_or_404(str(payload["credenciado_id"]))
    if not credenciado:
        return json_error("Credenciado não encontrado.", 404)

    vinculo = PrepostoCredenciadoVinculo.query.filter_by(
        credenciado_id=credenciado.id,
        preposto_id=preposto.id,
        localidade_id=localidade.id,
    ).first()
    if vinculo:
        vinculo.ativo = True
        vinculo.observacoes = payload.get("observacoes")
    else:
        vinculo = PrepostoCredenciadoVinculo(
            credenciado_id=credenciado.id,
            preposto_id=preposto.id,
            localidade_id=localidade.id,
            ativo=True,
            observacoes=payload.get("observacoes"),
        )
        db.session.add(vinculo)

    db.session.commit()
    return jsonify(PrepostoSchema().dump(preposto)), 201


@prepostos_bp.delete(
    "/<uuid:preposto_id>/localidades/<uuid:localidade_id>/credenciados/<uuid:credenciado_id>"
)
@admin_required
def delete_preposto_credenciado_vinculo(
    preposto_id, localidade_id, credenciado_id
):
    preposto = get_preposto_or_404(str(preposto_id))
    if not preposto:
        return json_error("Preposto não encontrado.", 404)

    localidade = get_localidade_or_404(str(preposto_id), str(localidade_id))
    if not localidade:
        return json_error("Localidade não encontrada para este preposto.", 404)

    vinculo = PrepostoCredenciadoVinculo.query.filter_by(
        credenciado_id=as_uuid(credenciado_id),
        preposto_id=preposto.id,
        localidade_id=localidade.id,
    ).first()
    if not vinculo:
        return json_error("Vínculo não encontrado.", 404)

    vinculo.ativo = False
    db.session.commit()
    return jsonify({"message": "Vínculo removido com sucesso."}), 200


@prepostos_bp.get("/public/lookup")
def lookup_prepostos():
    params = request.args

    cidade = params.get("cidade", "").strip()
    operacao = params.get("operacao")

    if operacao not in ("IMPORTACAO", "EXPORTACAO"):
        return json_error(
            "Operação inválida. Utilize IMPORTACAO ou EXPORTACAO.",
            422,
        )

    principal_contact_subquery = (
        db.session.query(
            PrepostoContato.id.label("contato_id"),
            PrepostoContato.preposto_id.label("preposto_id"),
            func.row_number()
            .over(
                partition_by=PrepostoContato.preposto_id,
                order_by=PrepostoContato.created_at.asc(),
            )
            .label("rn"),
        )
        .filter(PrepostoContato.principal.is_(True))
        .subquery()
    )

    q = (
        db.session.query(
            Preposto.id.label("id"),
            PrepostoLocalidade.id.label("localidade_id"),
            Preposto.nome.label("nome"),
            PrepostoLocalidade.cidade.label("cidade"),
            PrepostoLocalidade.uf.label("uf"),
            PrepostoLocalidade.descricao_local.label("descricao_local"),
            PrepostoLocalidade.moeda.label("moeda"),
            PrepostoLocalidade.observacoes.label("observacoes"),
            PrepostoLocalidade.valor_importacao.label("valor_importacao"),
            PrepostoLocalidade.valor_exportacao.label("valor_exportacao"),
            PrepostoLocalidade.valor_importacao_descricao.label("valor_importacao_descricao"),
            PrepostoLocalidade.valor_exportacao_descricao.label("valor_exportacao_descricao"),
            PrepostoContato.nome.label("contato_nome"),
            PrepostoContato.email.label("email"),
            PrepostoContato.telefone.label("telefone"),
        )
        .join(PrepostoLocalidade, PrepostoLocalidade.preposto_id == Preposto.id)
        .outerjoin(
            principal_contact_subquery,
            and_(
                principal_contact_subquery.c.preposto_id == Preposto.id,
                principal_contact_subquery.c.rn == 1,
            ),
        )
        .outerjoin(
            PrepostoContato,
            PrepostoContato.id == principal_contact_subquery.c.contato_id,
        )
        .filter(Preposto.ativo.is_(True))
    )

    if cidade:
        pattern = f"%{cidade}%"
        q = q.filter(
            or_(
                PrepostoLocalidade.cidade.ilike(pattern),
                PrepostoLocalidade.uf.ilike(pattern),
                PrepostoLocalidade.descricao_local.ilike(pattern),
                Preposto.nome.ilike(pattern),
            )
        )

    if operacao == "IMPORTACAO":
        q = q.filter(PrepostoLocalidade.atende_importacao.is_(True))
    elif operacao == "EXPORTACAO":
        q = q.filter(PrepostoLocalidade.atende_exportacao.is_(True))

    rows = q.order_by(Preposto.nome.asc()).all()

    locality_ids = [row.localidade_id for row in rows]
    tariffs_by_locality = {locality_id: [] for locality_id in locality_ids}
    credentials_by_locality = {locality_id: [] for locality_id in locality_ids}

    if locality_ids:
        tariff_rows = (
            PrepostoTarifa.query.filter(
                PrepostoTarifa.localidade_id.in_(locality_ids),
                PrepostoTarifa.ativo.is_(True),
                PrepostoTarifa.operacao.in_((operacao, "AMBAS")),
            )
            .order_by(
                PrepostoTarifa.principal.desc(),
                PrepostoTarifa.condicao.asc(),
            )
            .all()
        )
        for tariff in tariff_rows:
            tariffs_by_locality[tariff.localidade_id].append(
                {
                    "id": str(tariff.id),
                    "codigo": tariff.codigo,
                    "tipo": tariff.tipo,
                    "operacao": tariff.operacao,
                    "valor": float(tariff.valor) if tariff.valor is not None else None,
                    "valorDescricao": tariff.valor_descricao,
                    "condicao": tariff.condicao,
                    "principal": tariff.principal,
                    "moeda": tariff.moeda or "BRL",
                    "observacoes": tariff.observacoes,
                }
            )

        credential_rows = (
            db.session.query(
                PrepostoCredenciadoVinculo.localidade_id.label("localidade_id"),
                PrepostoCredenciado.id.label("id"),
                PrepostoCredenciado.nome.label("nome"),
                PrepostoCredenciado.cpf.label("cpf"),
                PrepostoCredenciado.registro_rfb.label("registro_rfb"),
                PrepostoCredenciado.categoria.label("categoria"),
            )
            .join(
                PrepostoCredenciado,
                PrepostoCredenciado.id
                == PrepostoCredenciadoVinculo.credenciado_id,
            )
            .filter(
                PrepostoCredenciadoVinculo.localidade_id.in_(locality_ids),
                PrepostoCredenciadoVinculo.ativo.is_(True),
                PrepostoCredenciado.ativo.is_(True),
            )
            .order_by(PrepostoCredenciado.nome.asc())
            .all()
        )
        for credential in credential_rows:
            cpf = "".join(
                character
                for character in (credential.cpf or "")
                if character.isdigit()
            )
            cpf_masked = (
                f"***.{cpf[3:6]}.{cpf[6:9]}-**" if len(cpf) == 11 else None
            )
            credentials_by_locality[credential.localidade_id].append(
                {
                    "id": str(credential.id),
                    "nome": credential.nome,
                    "cpfMascarado": cpf_masked,
                    "registroRfb": credential.registro_rfb,
                    "categoria": credential.categoria,
                }
            )

    items = []
    for row in rows:
        if operacao == "IMPORTACAO":
            valor = row.valor_importacao
            valor_descricao = row.valor_importacao_descricao
        else:
            valor = row.valor_exportacao
            valor_descricao = row.valor_exportacao_descricao

        items.append(
            {
                "id": str(row.id),
                "localidadeId": str(row.localidade_id),
                "nome": row.nome,
                "cidade": row.cidade,
                "uf": row.uf,
                "descricaoLocal": row.descricao_local,
                "operacao": operacao,
                "valor": float(valor) if valor is not None else None,
                "valorDescricao": valor_descricao,
                "moeda": row.moeda or "BRL",
                "telefone": row.telefone,
                "email": row.email,
                "contatoNome": row.contato_nome,
                "observacoes": row.observacoes,
                "tarifas": tariffs_by_locality[row.localidade_id],
                "credenciados": credentials_by_locality[row.localidade_id],
            }
        )

    payload = {
        "items": items,
        "total": len(items),
    }

    return jsonify(PrepostoLookupResponseSchema().dump(payload)), 200
