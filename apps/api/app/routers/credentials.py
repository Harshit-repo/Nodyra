from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Credential
from app.schemas import CredentialCreate, CredentialInfo, CredentialUpdate
from app.services.audit import log_audit
from app.services.crypto import decrypt_data, encrypt_data

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _info(cred: Credential) -> CredentialInfo:
    data = decrypt_data(cred.encrypted_data)
    return CredentialInfo(
        id=cred.id,
        name=cred.name,
        type=cred.type,
        keys=sorted(data.keys()),
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )


async def _load(session: AsyncSession, cred_id: str) -> Credential:
    cred = await session.get(Credential, cred_id)
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    return cred


@router.get("", response_model=list[CredentialInfo])
async def list_credentials(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(Credential).order_by(Credential.name))
    return [_info(c) for c in result.all()]


@router.post("", response_model=CredentialInfo, status_code=status.HTTP_201_CREATED)
async def create_credential(
    body: CredentialCreate, session: AsyncSession = Depends(get_session)
):
    cred = Credential(
        name=body.name,
        type=body.type,
        encrypted_data=encrypt_data(body.data),
    )
    session.add(cred)
    await log_audit(session, "create", "credential", detail=body.name)
    await session.commit()
    await session.refresh(cred)
    return _info(cred)


@router.put("/{cred_id}", response_model=CredentialInfo)
async def update_credential(
    cred_id: str,
    body: CredentialUpdate,
    session: AsyncSession = Depends(get_session),
):
    cred = await _load(session, cred_id)
    if body.name is not None:
        cred.name = body.name
    if body.data is not None:
        cred.encrypted_data = encrypt_data(body.data)
    await log_audit(session, "update", "credential", cred.id, cred.name)
    await session.commit()
    await session.refresh(cred)
    return _info(cred)


@router.delete("/{cred_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    cred_id: str, session: AsyncSession = Depends(get_session)
):
    cred = await _load(session, cred_id)
    await log_audit(session, "delete", "credential", cred.id, cred.name)
    await session.delete(cred)
    await session.commit()
