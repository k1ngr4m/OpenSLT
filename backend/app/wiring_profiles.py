from __future__ import annotations

import typing
from ipaddress import IPv4Address

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WiringInterface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    ip_address: IPv4Address

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("接口名称不能为空")
        return value


class RemWiringProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_switch_label: str = Field(min_length=1, max_length=128)
    market_switch_label: str = Field(min_length=1, max_length=128)
    client_interface: WiringInterface
    market_interface: WiringInterface

    @field_validator("client_switch_label", "market_switch_label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("交换机标签不能为空")
        return value


BUSINESS_TOPOLOGY = {
    "fut_mm": ("soft_core", "软核"),
    "rem_two": ("hard_core_nf11", "NF11"),
    "rem_two_mm": ("hard_core_mg11", "MG11"),
}

SLNIC_PORT_MAPPING = [
    {"port": 0, "side": "client", "direction": "uplink", "label": "客户端上行"},
    {"port": 1, "side": "market", "direction": "uplink", "label": "市场上行"},
    {"port": 2, "side": "market", "direction": "downlink", "label": "市场下行"},
    {"port": 3, "side": "client", "direction": "downlink", "label": "客户端下行"},
]


def parse_wiring_profile(value: typing.Any) -> RemWiringProfile:
    return RemWiringProfile.model_validate(value)


def build_wiring_snapshot(rem: typing.Any, slnic: typing.Any, business_code: str) -> dict[str, typing.Any]:
    profile = parse_wiring_profile(rem.wiring_profile)
    topology_kind, model_label = BUSINESS_TOPOLOGY[business_code]
    return {
        "schema_version": 1,
        "business_code": business_code,
        "topology_kind": topology_kind,
        "model_label": model_label,
        "client_switch_label": profile.client_switch_label,
        "market_switch_label": profile.market_switch_label,
        "client_interface": profile.client_interface.model_dump(mode="json"),
        "market_interface": profile.market_interface.model_dump(mode="json"),
        "auxiliary_interfaces": [] if business_code == "fut_mm" else ["3(mac2)", "4(mac3)"],
        "rem": {"id": rem.id, "name": rem.name, "host": rem.host},
        "slnic": {"id": slnic.id, "name": slnic.name, "host": slnic.host},
        "slnic_ports": list(SLNIC_PORT_MAPPING),
    }
