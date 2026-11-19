"""DV Schema Explorer & Read-Only DuckDB Sandbox Query router (`D11`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from opendv_contracts.dtos.dv_schema import (
    DvSchemaProjectionDTO,
    DvSchemaQueryRequestDTO,
    DvSchemaQueryResponseDTO,
    DvSchemaTableRowsDTO,
)
from opendv_platform_api.deps import get_opendv_service
from opendv_service import InputValidationError, NotFoundError, OpenDVService
from opendv_service.use_cases.dv_schema_explorer import (
    execute_dv_schema_query,
    get_dv_schema_projection,
    get_dv_schema_table_rows,
)

router = APIRouter(tags=["dv-schema"])


@router.get(
    "/runs/{run_id}/dv-schema",
    response_model=DvSchemaProjectionDTO,
    summary="Get Data Vault 2.0 Schema Explorer projection for a run",
)
async def get_dv_schema_endpoint(
    run_id: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    compare: Annotated[
        str | None,
        Query(description="Optional baseline run_id to compute multi-wave schema diff."),
    ] = None,
) -> DvSchemaProjectionDTO:
    """Return DV 2.0 Hubs, Links, Satellites, triage ledger, and optional wave diff."""
    try:
        return get_dv_schema_projection(service.state, run_id, compare_run_id=compare)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InputValidationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/runs/{run_id}/dv-schema/tables/{table}/rows",
    response_model=DvSchemaTableRowsDTO,
    summary="Preview rows from a persisted DuckDB sandbox table",
)
async def get_dv_schema_table_rows_endpoint(
    run_id: str,
    table: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DvSchemaTableRowsDTO:
    """Return paginated read-only rows from a persisted run sandbox `.duckdb` table."""
    try:
        return get_dv_schema_table_rows(
            service.state,
            run_id,
            table,
            limit=limit,
            offset=offset,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InputValidationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/runs/{run_id}/dv-schema/query",
    response_model=DvSchemaQueryResponseDTO,
    summary="Execute read-only SQL against a persisted DuckDB sandbox",
)
async def query_dv_schema_endpoint(
    run_id: str,
    req: DvSchemaQueryRequestDTO,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> DvSchemaQueryResponseDTO:
    """Validate SQL against the strict allowlist and execute read-only in DuckDB."""
    try:
        return execute_dv_schema_query(
            service.state,
            run_id,
            sql=req.sql,
            max_rows=req.max_rows,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InputValidationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
