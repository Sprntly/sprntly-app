"""Customer-facing MCP (Model Context Protocol) token management.

Lets a signed-in user generate/list/revoke a bearer token their own AI client
(Claude Desktop, Claude Code, claude.ai custom connectors) uses to connect to
their Sprntly workspace via the `mcp/` service. See app/db/mcp_tokens.py for
persistence and app/routes/internal_mcp.py for the machine-to-machine side
that `mcp/` actually calls to resolve a presented token.

Tenancy: require_company resolves the active company + user from the JWT, so
every token is scoped to the caller's own company and can never read/act on
another tenant's data.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.db.mcp_tokens import create_mcp_token, list_mcp_tokens, revoke_mcp_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/mcp-tokens", tags=["mcp-tokens"])


class CreateTokenIn(BaseModel):
    name: str = Field(default="MCP token", max_length=100)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(
    body: CreateTokenIn,
    company: CompanyContext = Depends(require_company),
):
    """Mint a new token. The raw token is returned ONCE, here, and never
    again — the frontend must show it in a copy-once banner."""
    row = create_mcp_token(
        company_id=company.company_id, user_id=company.user_id, name=body.name
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "token": row["token"],
        "token_prefix": row["token_prefix"],
        "created_at": row["created_at"],
    }


@router.get("")
def list_tokens(company: CompanyContext = Depends(require_company)):
    return {"tokens": list_mcp_tokens(company.company_id)}


@router.delete("/{token_id}")
def delete_token(
    token_id: str,
    company: CompanyContext = Depends(require_company),
):
    if not revoke_mcp_token(company.company_id, token_id):
        raise HTTPException(404, "Token not found")
    return {"ok": True}
