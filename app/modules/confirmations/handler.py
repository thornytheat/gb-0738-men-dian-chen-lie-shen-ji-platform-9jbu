from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config import Options
from app.errors import AuditError
from app.modules.confirmations.service import build_confirmation
from app.pipeline import Audit

router = APIRouter(prefix='/api/confirmations')
ConfirmationId = Annotated[str, Path(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset: Literal['field', 'reference'] = 'field'
    key: Annotated[str, Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{0,31}:[A-Z0-9][A-Z0-9_-]{0,31}$')]
    plan: Annotated[str, Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{0,31}$')]
    scope: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)] = 'slot'
    request_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')]
    actor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    note: Text
    evidence_ref: Text


@router.get('')
def inspect(request: Request, dataset: Literal['field', 'reference'] = 'field'):
    rows = sorted(request.app.state.store.confirmations(dataset=dataset).values(), key=lambda row: row['key'])
    return {'dataset': dataset, 'confirmations': rows}


@router.post('')
def confirm(request: Request, form: ConfirmationInput):
    if form.scope != 'slot':
        raise AuditError('SCOPE_TOO_BROAD', '叠期确认只能逐柜登记', 422, form.scope)
    plans = Audit(request.app.state.settings, Options(dataset=form.dataset), request.app.state.store).plans
    record, reused = request.app.state.store.save_confirmation(
        form.model_dump(), partial(build_confirmation, plans['decisions']))
    return {'confirmation': record, 'reused': reused}


@router.get('/{confirmation_id}')
def read(request: Request, confirmation_id: ConfirmationId):
    return request.app.state.store.get_confirmation(confirmation_id)
