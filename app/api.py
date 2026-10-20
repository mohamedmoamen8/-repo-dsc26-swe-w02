"""Tawseel parcel API — HTTP layer.

Expose create_app() -> FastAPI. Each call builds a fresh app with its own empty
in-memory parcel store (no module-level parcel dicts or counters). A module
level `app = create_app()` is provided for uvicorn.

Errors are RFC 9457 application/problem+json documents with at least type,
title and status; 422 responses additionally carry an `errors` list.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.domain import (
    EventType,
    InvalidTransition,
    Parcel,
    ParcelState,
    apply_event,
)


class AppError(Exception):
    """Application error rendered as an RFC 9457 problem document."""

    def __init__(
        self,
        status_code: int,
        title: str,
        type_: str = "about:blank",
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status_code = status_code
        self.title = title
        self.type = type_
        self.errors = errors


def _problem(
    status_code: int,
    title: str,
    type_: str = "about:blank",
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"type": type_, "title": title, "status": status_code}
    if errors is not None:
        body["errors"] = errors
    return body


def _parcel_to_dict(parcel: Parcel) -> dict[str, Any]:
    return {
        "id": parcel.id,
        "merchant_ref": parcel.merchant_ref,
        "cod_amount_piastres": parcel.cod_amount_piastres,
        "address_text": parcel.address_text,
        "state": parcel.state.value,
        "attempts": parcel.attempts,
    }


def _encode_cursor(seq: int) -> str:
    return base64.urlsafe_b64encode(str(seq).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> int:
    padded = cursor + "=" * (-len(cursor) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode()).decode()
    return int(decoded)


def _route_operation_id(route) -> str:
    path = route.path
    methods = getattr(route, "methods", None) or set()
    method = next(iter(sorted(methods))) if methods else ""
    mapping = {
        ("/v1/parcels", "POST"): "createParcel",
        ("/v1/parcels/{parcel_id}", "GET"): "getParcel",
        ("/v1/parcels", "GET"): "listParcels",
        ("/v1/parcels/{parcel_id}/events", "POST"): "recordParcelEvent",
    }
    return mapping.get((path, method), f"{method.lower()}_{path.strip('/').replace('/', '_')}")


# ------------------------------------------------------------------ schemas

class ParcelCreate(BaseModel):
    merchant_ref: str = Field(min_length=1, max_length=64)
    cod_amount_piastres: int = Field(ge=0, le=5_000_000)
    address_text: str = Field(min_length=1, max_length=500)


class EventCreate(BaseModel):
    type: str


class ParcelOut(BaseModel):
    id: str
    merchant_ref: str
    cod_amount_piastres: int
    address_text: str
    state: str
    attempts: int


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tawseel Parcel API",
        version="0.1.0",
        generate_unique_id_function=_route_operation_id,
    )

    # Fresh in-memory store per app instance.
    app.state.parcels: dict[str, Parcel] = {}
    app.state.order: list[str] = []  # creation order of parcel ids
    app.state.seq: dict[str, int] = {}  # parcel id -> creation sequence
    app.state.counter: int = 0

    # ------------------------------------------------------------------ errors

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_problem(exc.status_code, exc.title, exc.type, exc.errors),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"loc": list(err["loc"]), "msg": err["msg"]} for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_problem(422, "Validation error", "about:blank", errors),
            media_type="application/problem+json",
        )

    # ------------------------------------------------------------------ routes

    @app.post(
        "/v1/parcels",
        status_code=201,
        responses={
            201: {"description": "Parcel created", "model": ParcelOut},
            409: {"description": "Duplicate merchant_ref"},
            422: {"description": "Invalid body"},
        },
    )
    def create_parcel(parcel_in: ParcelCreate) -> JSONResponse:
        for existing in app.state.parcels.values():
            if existing.merchant_ref == parcel_in.merchant_ref:
                raise AppError(
                    status_code=409,
                    title="Duplicate merchant reference",
                    type_="https://tawseel.local/problems/duplicate-merchant-ref",
                )
        app.state.counter += 1
        parcel_id = f"pcl_{app.state.counter}"
        parcel = Parcel(
            id=parcel_id,
            merchant_ref=parcel_in.merchant_ref,
            cod_amount_piastres=parcel_in.cod_amount_piastres,
            address_text=parcel_in.address_text,
        )
        app.state.parcels[parcel_id] = parcel
        app.state.order.append(parcel_id)
        app.state.seq[parcel_id] = app.state.counter
        return JSONResponse(
            status_code=201,
            content=_parcel_to_dict(parcel),
            headers={"Location": f"/v1/parcels/{parcel_id}"},
            media_type="application/json",
        )

    @app.get(
        "/v1/parcels/{parcel_id}",
        responses={
            200: {"description": "The parcel", "model": ParcelOut},
            404: {"description": "Parcel not found"},
        },
    )
    def get_parcel(parcel_id: str = Path(...)) -> dict[str, Any]:
        parcel = app.state.parcels.get(parcel_id)
        if parcel is None:
            raise AppError(
                status_code=404,
                title="Parcel not found",
                type_="https://tawseel.local/problems/not-found",
            )
        return _parcel_to_dict(parcel)

    @app.get(
        "/v1/parcels",
        responses={
            200: {"description": "Page of parcels"},
            400: {"description": "Bad cursor"},
            422: {"description": "Bad state or limit"},
        },
    )
    def list_parcels(
        state: str | None = Query(None),
        limit: int = Query(20, ge=1, le=100),
        cursor: str | None = Query(None),
    ) -> dict[str, Any]:
        state_enum: ParcelState | None = None
        if state is not None:
            try:
                state_enum = ParcelState(state)
            except ValueError:
                raise AppError(
                    status_code=422,
                    title="Unknown state",
                    type_="https://tawseel.local/problems/unknown-state",
                    errors=[{"loc": ["query", "state"], "msg": "Unknown state value"}],
                )

        cursor_seq: int | None = None
        if cursor is not None:
            try:
                cursor_seq = _decode_cursor(cursor)
                if cursor_seq < 0:
                    raise ValueError
            except Exception:
                raise AppError(
                    status_code=400,
                    title="Invalid cursor",
                    type_="https://tawseel.local/problems/invalid-cursor",
                    errors=[{"loc": ["query", "cursor"], "msg": "Cursor is not valid"}],
                )

        result: list[tuple[int, Parcel]] = []
        for parcel_id in app.state.order:
            seq = app.state.seq[parcel_id]
            if cursor_seq is not None and seq <= cursor_seq:
                continue
            parcel = app.state.parcels[parcel_id]
            if state_enum is not None and parcel.state != state_enum:
                continue
            result.append((seq, parcel))
            if len(result) == limit:
                break

        items = [_parcel_to_dict(p) for _seq, p in result]

        next_cursor: str | None = None
        if result:
            last_seq = result[-1][0]
            found_more = False
            for parcel_id in app.state.order:
                seq = app.state.seq[parcel_id]
                if seq <= last_seq:
                    continue
                parcel = app.state.parcels[parcel_id]
                if state_enum is not None and parcel.state != state_enum:
                    continue
                found_more = True
                break
            if found_more:
                next_cursor = _encode_cursor(last_seq)

        return {"items": items, "next_cursor": next_cursor}

    @app.post(
        "/v1/parcels/{parcel_id}/events",
        responses={
            200: {"description": "Updated parcel", "model": ParcelOut},
            404: {"description": "Parcel not found"},
            409: {"description": "Invalid transition"},
            422: {"description": "Unknown or missing event type"},
        },
    )
    def record_parcel_event(
        event_in: EventCreate, parcel_id: str = Path(...)
    ) -> dict[str, Any]:
        parcel = app.state.parcels.get(parcel_id)
        if parcel is None:
            raise AppError(
                status_code=404,
                title="Parcel not found",
                type_="https://tawseel.local/problems/not-found",
            )

        try:
            event = EventType(event_in.type)
        except ValueError:
            raise AppError(
                status_code=422,
                title="Unknown event type",
                type_="https://tawseel.local/problems/unknown-event-type",
                errors=[{"loc": ["body", "type"], "msg": "Unknown or missing event type"}],
            )

        try:
            new_parcel = apply_event(parcel, event)
        except InvalidTransition:
            raise AppError(
                status_code=409,
                title="Invalid transition",
                type_="https://tawseel.local/problems/invalid-transition",
                errors=[
                    {
                        "loc": ["body", "type"],
                        "msg": f"Cannot {event.value} a parcel in state {parcel.state.value}",
                    }
                ],
            )

        app.state.parcels[parcel_id] = new_parcel
        return _parcel_to_dict(new_parcel)

    return app


app = create_app()
