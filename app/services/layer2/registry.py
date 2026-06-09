from __future__ import annotations

from collections.abc import Callable

from app.schemas import BlockResponse, ValidatedShipmentRequest
from app.services.layer2.connectors.air_a_connector import fetch_air_a
from app.services.layer2.connectors.air_b_connector import fetch_air_b
from app.services.layer2.connectors.air_c_connector import fetch_air_c
from app.services.layer2.connectors.air_d_connector import fetch_air_d
from app.services.layer2.connectors.air_e_connector import fetch_air_e
from app.services.layer2.connectors.air_f_connector import fetch_air_f
from app.services.layer2.connectors.air_h_connector import fetch_air_h
from app.services.layer2.connectors.air_i_connector import fetch_air_i
from app.services.layer2.connectors.road_a_connector import fetch_road_a
from app.services.layer2.connectors.road_b_connector import fetch_road_b
from app.services.layer2.connectors.road_c_connector import fetch_road_c
from app.services.layer2.connectors.road_cost_connector import fetch_road_cost
from app.services.layer2.connectors.road_f_connector import fetch_road_f
from app.services.layer2.connectors.sea_a_connector import fetch_sea_a
from app.services.layer2.connectors.sea_b_connector import fetch_sea_b
from app.services.layer2.connectors.sea_cost_connector import fetch_sea_cost
from app.services.layer2.connectors.sea_c_connector import fetch_sea_c
from app.services.layer2.connectors.sea_d_connector import fetch_sea_d
from app.services.layer2.connectors.sea_f_connector import fetch_sea_f
from app.services.layer2.connectors.sea_i_connector import fetch_sea_i

Connector = Callable[[ValidatedShipmentRequest], BlockResponse]


def _fetch_road_c_from_request(request: ValidatedShipmentRequest) -> BlockResponse:
    return fetch_road_c(
        request.lane.origin_country,
        request.lane.destination_country,
    )


BLOCK_REGISTRY: dict[str, Connector] = {
    "AIR-A": fetch_air_a,
    "AIR-B": fetch_air_b,
    "AIR-C": fetch_air_c,
    "AIR-D": fetch_air_d,
    "AIR-E": fetch_air_e,
    "AIR-F": fetch_air_f,
    "AIR-H": fetch_air_h,
    "AIR-I": fetch_air_i,
    "ROAD-C": _fetch_road_c_from_request,
    "ROAD-A": fetch_road_a,
    "ROAD-B": fetch_road_b,
    "ROAD-F": fetch_road_f,
    "ROAD-COST": fetch_road_cost,
    "SEA-C": fetch_sea_c,
    "SEA-D": fetch_sea_d,
    "SEA-A": fetch_sea_a,
    "SEA-B": fetch_sea_b,
    "SEA-F": fetch_sea_f,
    "SEA-I": fetch_sea_i,
    "SEA-COST": fetch_sea_cost,
}


def get_connector(block_id: str) -> Connector | None:
    return BLOCK_REGISTRY.get(block_id)
