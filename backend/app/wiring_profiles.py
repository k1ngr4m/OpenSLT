from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface, IPv4Network


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


def resolve_rem_wiring_interfaces(value: str) -> tuple[dict, dict]:
    client_network = IPv4Network("180.0.0.0/8")
    market_network = IPv4Network("10.1.51.0/24")
    client_candidates: list[dict[str, str]] = []
    market_candidates: list[dict[str, str]] = []
    for line in value.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, address_with_prefix = parts
        try:
            address = IPv4Interface(address_with_prefix).ip
        except ValueError:
            continue
        candidate = {"name": name, "ip_address": str(address)}
        if address in client_network:
            client_candidates.append(candidate)
        elif address in market_network:
            market_candidates.append(candidate)
    empty = {"name": "", "ip_address": ""}
    return (
        client_candidates[0] if len(client_candidates) == 1 else dict(empty),
        market_candidates[0] if len(market_candidates) == 1 else dict(empty),
    )


def refresh_pending_wiring_snapshots(run, source_step, value: str) -> int:
    client_interface, market_interface = resolve_rem_wiring_interfaces(value)
    updated = 0
    for step in run.steps:
        if (
            step.position <= source_step.position
            or step.node_type != "wiring_confirmation"
            or step.status != "pending"
        ):
            continue
        config = dict(step.config_snapshot or {})
        snapshot_value = config.get("wiring_snapshot")
        if not isinstance(snapshot_value, dict):
            continue
        snapshot = dict(snapshot_value)
        snapshot["client_interface"] = dict(client_interface)
        snapshot["market_interface"] = dict(market_interface)
        config["wiring_snapshot"] = snapshot
        step.config_snapshot = config
        updated += 1
    return updated


def wiring_interfaces_complete(snapshot: object) -> bool:
    if not isinstance(snapshot, dict):
        return False
    for key in ("client_interface", "market_interface"):
        interface = snapshot.get(key)
        if not isinstance(interface, dict) or not str(interface.get("name") or "").strip():
            return False
        try:
            IPv4Address(str(interface.get("ip_address") or ""))
        except ValueError:
            return False
    return True


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
    _, _, auxiliary_names = wiring_interface_names(
        business_code,
        client_interface_name=client_interface_name,
        market_interface_name=market_interface_name,
        auxiliary_interface_names=auxiliary_interface_names,
    )
    market_ip = str(IPv4Address(market.host))
    slnic_ip = str(IPv4Address(slnic.host))
    return {
        "schema_version": 2,
        "business_code": business_code,
        "topology_kind": preset["topology_kind"],
        "model_label": preset["model_label"],
        "client_switch_label": preset["client_switch_label"],
        "market_switch_label": preset["market_switch_label"],
        "client_interface": {"name": "", "ip_address": ""},
        "market_interface": {"name": "", "ip_address": ""},
        "auxiliary_interfaces": auxiliary_names,
        "rem": {"id": rem.id, "name": rem.name, "host": rem.host},
        "market": {"id": market.id, "name": market.name, "host": market_ip},
        "slnic": {"id": slnic.id, "name": slnic.name, "host": slnic_ip},
        "slnic_ports": list(SLNIC_PORT_MAPPING),
    }
