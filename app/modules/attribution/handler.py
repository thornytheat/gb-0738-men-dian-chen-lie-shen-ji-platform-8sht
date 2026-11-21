from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config import Options
from app.manifest import canonical
from app.modules.attribution.reconcile import reconcile_attribution
from app.modules.attribution.service import batch_key, build_attribution
from app.modules.attribution.transitions import next_confirmation
from app.pipeline import Audit
from app.reasons import REASONS

router = APIRouter(prefix='/api/attribution')
BatchId = Annotated[str, Path(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{8,64}$')]
    expected_version: Annotated[int, Field(ge=0, le=1)] = 0
    actor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    note: Text
    evidence_ref: Text


def _build(request, options):
    audit = Audit(request.app.state.settings, options)
    return build_attribution(audit.intake, audit.plans, audit.photos, batch_key(request.app.state.settings, options))


@router.get('')
def preview(request: Request, options: Annotated[Options, Query()]):
    return _build(request, options)


@router.post('/batches')
def create(request: Request, options: Options):
    payload = _build(request, options)
    snapshot, reused = request.app.state.store.save_attribution(payload)
    return {'reused': reused, **snapshot}


@router.get('/batches/{batch_id}')
def read_batch(request: Request, batch_id: BatchId):
    return request.app.state.store.get_attribution(batch_id)


@router.post('/batches/{batch_id}/confirm')
def confirm(request: Request, batch_id: BatchId, form: ConfirmInput):
    store = request.app.state.store
    # 发布前再做一次完整勾稽；失败直接抛错，事务不写入、旧版草稿原样保留
    reconcile_attribution(store.get_attribution(batch_id))
    event, reused = store.confirm_attribution(
        batch_id, form.model_dump(), next_confirmation)
    return {'event': event, 'reused': reused,
            'batch': store.get_attribution(batch_id)}


@router.get('/batches/{batch_id}/evidence')
def evidence(request: Request, batch_id: BatchId):
    snapshot = request.app.state.store.get_attribution(batch_id)
    packet = {'format': 'shelf-evidence/attribution-1', **snapshot, 'reason_dictionary': REASONS}
    return Response(canonical(packet) + '\n', media_type='application/json',
                    headers={'Content-Disposition': f'attachment; filename="attribution-{batch_id[:12]}.json"'})
