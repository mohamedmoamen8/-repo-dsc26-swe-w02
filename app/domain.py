"""Tawseel parcel lifecycle — pure Python domain model.

No framework imports (fastapi, starlette, pydantic, sqlalchemy) so this module
can be tested in isolation and reasoned about without any HTTP or persistence
concerns. The lifecycle rules live here and the HTTP layer (app/api.py) calls
apply_event for every state change, keeping the business rules in one place
as they evolve.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

MAX_ATTEMPTS = 3


class ParcelState(StrEnum):
    CREATED = "created"
    OUT_FOR_DELIVERY = "out_for_delivery"
    FAILED_ATTEMPT = "failed_attempt"
    DELIVERED = "delivered"
    RETURNED = "returned"


class EventType(StrEnum):
    DISPATCH = "dispatch"
    DELIVER = "deliver"
    FAIL = "fail"
    RETURN = "return"


class InvalidTransition(Exception):
    """Raised when an event is not allowed for the current parcel state."""


@dataclass(frozen=True)
class Parcel:
    id: str
    merchant_ref: str
    cod_amount_piastres: int
    address_text: str
    state: ParcelState = ParcelState.CREATED
    attempts: int = 0


def apply_event(parcel: Parcel, event: EventType | str) -> Parcel:
    """Return a new Parcel with the event applied.

    The input parcel is never mutated. Only `state` and `attempts` may change.
    Raises InvalidTransition if the event is not allowed for the current state.
    """
    if not isinstance(event, EventType):
        event = EventType(event)
    if parcel.state is ParcelState.CREATED:
        if event is EventType.DISPATCH:
            return replace(
                parcel,
                state=ParcelState.OUT_FOR_DELIVERY,
                attempts=parcel.attempts + 1,
            )
        raise InvalidTransition(
            f"Cannot {event.value} a parcel in state {parcel_state_value(parcel.state)}"
        )

    if parcel.state is ParcelState.OUT_FOR_DELIVERY:
        if event is EventType.DELIVER:
            return replace(parcel, state=ParcelState.DELIVERED)
        if event is EventType.FAIL:
            return replace(parcel, state=ParcelState.FAILED_ATTEMPT)
        raise InvalidTransition(
            f"Cannot {event.value} a parcel in state {parcel_state_value(parcel.state)}"
        )

    if parcel.state is ParcelState.FAILED_ATTEMPT:
        if event is EventType.RETURN:
            return replace(parcel, state=ParcelState.RETURNED)
        if event is EventType.DISPATCH:
            if parcel.attempts < MAX_ATTEMPTS:
                return replace(
                    parcel,
                    state=ParcelState.OUT_FOR_DELIVERY,
                    attempts=parcel.attempts + 1,
                )
            raise InvalidTransition(
                f"Cannot {event.value}: maximum attempts ({MAX_ATTEMPTS}) reached"
            )
        raise InvalidTransition(
            f"Cannot {event.value} a parcel in state {parcel_state_value(parcel.state)}"
        )

    # DELIVERED and RETURNED are final.
    raise InvalidTransition(
        f"Cannot {event.value} a parcel in state {parcel_state_value(parcel.state)}"
    )


def parcel_state_value(state: ParcelState) -> str:
    return state.value if isinstance(state, ParcelState) else str(state)
