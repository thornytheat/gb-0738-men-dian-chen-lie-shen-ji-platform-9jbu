from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.config import Options
from app.pipeline import Audit

router = APIRouter(prefix='/api/overlaps')


@router.get('')
def inspect(request: Request, options: Annotated[Options, Query()]):
    plans = Audit(request.app.state.settings, options, request.app.state.store).plans
    return {'dataset': options.dataset, 'overlaps': plans['overlaps'], 'summary': plans['overlap_summary']}
