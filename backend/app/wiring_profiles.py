from __future__ import annotations

from ipaddress import IPv4Address


BUSINESS_TOPOLOGY = {
    "fut_mm": {
        "topology_kind": "soft_core",
        "model_label": "软核",
        "client_switch_label": "180段交换机（客户端）",
        "market_switch_label": "51段交换机（市场端）",
        "client_interface": "enp101s0d1",
        "market_interface": "enp23s0",
        "auxiliary_interfaces": [],
    },
    "rem_two": {
        "topology_kind": "hard_core_nf11",
        "model_label": "NF11",
        "client_switch_label": "51段交换机（客户端）",
        "market_switch_label": "51段交换机（市场端）",
        "client_interface": "1(mac0)",
        "market_interface": "2(mac1)",
        "auxiliary_interfaces": ["3(mac2)", "4(mac3)"],
    },
    "rem_two_mm": {
        "topology_kind": "hard_core_mg11",
        "model_label": "MG11",
        "client_switch_label": "51段交换机（客户端）",
        "market_switch_label": "51段交换机（市场端）",
        "client_interface": "1(mac0)",
        "market_interface": "2(mac1)",
        "auxiliary_interfaces": ["3(mac2)", "4(mac3)"],
    },
}

SLNIC_PORT_MAPPING = [
    {"port": 0, "side": "client", "direction": "uplink", "label": "客户端上行"},
    {"port": 1, "side": "market", "direction": "uplink", "label": "市场上行"},
    {"port": 2, "side": "market", "direction": "downlink", "label": "市场下行"},
    {"port": 3, "side": "client", "direction": "downlink", "label": "客户端下行"},
]


def wiring_interface_names(
    business_code: str,
    *,
    client_interface_name: str | None = None,
    market_interface_name: str | None = None,
    auxiliary_interface_names: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    preset = BUSINESS_TOPOLOGY[business_code]
    return (
        preset["client_interface"]
        if client_interface_name is None
        else client_interface_name,
        preset["market_interface"]
        if market_interface_name is None
        else market_interface_name,
        list(preset["auxiliary_interfaces"])
        if auxiliary_interface_names is None
        else list(auxiliary_interface_names),
    )


def build_wiring_snapshot(
    rem,
    market,
    slnic,
    business_code: str,
    *,
    client_interface_name: str | None = None,
    market_interface_name: str | None = None,
    auxiliary_interface_names: list[str] | None = None,
) -> dict:
    preset = BUSINESS_TOPOLOGY[business_code]
    client_name, market_name, auxiliary_names = wiring_interface_names(
        business_code,
        client_interface_name=client_interface_name,
        market_interface_name=market_interface_name,
        auxiliary_interface_names=auxiliary_interface_names,
    )
    client_ip = str(IPv4Address(rem.trade_ip))
    market_ip = str(IPv4Address(market.host))
    slnic_ip = str(IPv4Address(slnic.host))
    return {
        "schema_version": 2,
        "business_code": business_code,
        "topology_kind": preset["topology_kind"],
        "model_label": preset["model_label"],
        "client_switch_label": preset["client_switch_label"],
        "market_switch_label": preset["market_switch_label"],
        "client_interface": {"name": client_name, "ip_address": client_ip},
        "market_interface": {"name": market_name, "ip_address": market_ip},
        "auxiliary_interfaces": auxiliary_names,
        "rem": {"id": rem.id, "name": rem.name, "host": rem.host},
        "market": {"id": market.id, "name": market.name, "host": market_ip},
        "slnic": {"id": slnic.id, "name": slnic.name, "host": slnic_ip},
        "slnic_ports": list(SLNIC_PORT_MAPPING),
    }
