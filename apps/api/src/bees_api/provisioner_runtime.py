"""Canal fechado do provisionador; sem emissão de credenciais ou efeitos nativos."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from bees_api.provisioning import _service
from bees_core.provisioning import (
    BeginDispatchInput,
    ClaimBinding,
    ClaimInput,
    ReceiptInput,
    RenewInput,
    UnknownInput,
)

router = APIRouter(prefix="/api/v1/provisioner/runtime", tags=["provisioner-runtime"])


def _credential(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer bp_"):
        raise HTTPException(
            401,
            detail={
                "code": "provisioning_credentials_invalid",
                "message": "Credencial do provisionador inválida.",
            },
        )
    token = values[0][7:]
    # Autenticar antes da validação do DTO, além da reavaliação atômica no comando.
    # Cookies de usuário e credenciais bh_ nunca substituem esta autoridade.
    _service(request).session(token)
    return token


_CREDENTIAL = Annotated[str, Depends(_credential)]


@router.get("/session")
def session(request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).session(credential)


@router.post("/claim")
def claim(body: ClaimInput, request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).claim(credential, body)


@router.post("/current")
def current(body: ClaimBinding, request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).assert_current(credential, body)


@router.post("/renew")
def renew(body: RenewInput, request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).renew(credential, body)


@router.post("/begin")
def begin(body: BeginDispatchInput, request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).begin_dispatch(credential, body)


@router.post("/receipt")
def receipt(body: ReceiptInput, request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).record_receipt(credential, body)


@router.post("/unknown")
def unknown(body: UnknownInput, request: Request, credential: _CREDENTIAL) -> dict:
    return _service(request).mark_unknown(credential, body)
