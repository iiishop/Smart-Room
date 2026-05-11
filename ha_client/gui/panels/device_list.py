import tkinter as tk
from tkinter import ttk

from ha_client.core.device_manager import DeviceManager
from ha_client.gui.async_helper import AsyncTkHelper
from ha_client.models.entity import EntityDomain

DOMAIN_ICONS = {
    EntityDomain.LIGHT: "\u2600",
    EntityDomain.SWITCH: "\u23FB",
    EntityDomain.SENSOR: "\U0001F321",
    EntityDomain.BINARY_SENSOR: "\u26A0",
    EntityDomain.CLIMATE: "\u2744",
    EntityDomain.COVER: "\u2B07",
    EntityDomain.MEDIA_PLAYER: "\u25B6",
    EntityDomain.FAN: "\u2744",
    EntityDomain.LOCK: "\U0001F512",
    EntityDomain.SCENE: "\U0001F3AC",
    EntityDomain.AUTOMATION: "\u2699",
    EntityDomain.SCRIPT: "\U0001F4DC",
    EntityDomain.UNKNOWN: "?",
}

DOMAIN_LABELS = {
    EntityDomain.LIGHT: "Lights",
    EntityDomain.SWITCH: "Switches",
    EntityDomain.SENSOR: "Sensors",
    EntityDomain.BINARY_SENSOR: "Binary Sensors",
    EntityDomain.CLIMATE: "Climate",
    EntityDomain.COVER: "Covers",
    EntityDomain.MEDIA_PLAYER: "Media Players",
    EntityDomain.FAN: "Fans",
    EntityDomain.LOCK: "Locks",
    EntityDomain.SCENE: "Scenes",
    EntityDomain.AUTOMATION: "Automations",
    EntityDomain.SCRIPT: "Scripts",
    EntityDomain.UNKNOWN: "Other",
}


class DeviceListPanel:
    def __init__(
        self,
        parent: ttk.Frame,
        device_manager: DeviceManager,
        async_helper: AsyncTkHelper,
        on_select: callable,
    ):
        self._device_manager = device_manager
        self._async_helper = async_helper
        self._on_select = on_select

        frame = ttk.LabelFrame(parent, text="Devices", padding=4)
        frame.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(
            toolbar, text="Refresh", command=self._on_refresh_click
        ).pack(side=tk.RIGHT)

        columns = ("icon", "name", "state", "entity_id")
        self._tree = ttk.Treeview(frame, columns=columns, show="tree headings", selectmode="browse")

        self._tree.heading("icon", text="")
        self._tree.heading("name", text="Name")
        self._tree.heading("state", text="State")
        self._tree.heading("entity_id", text="Entity ID")

        self._tree.column("icon", width=30, minwidth=30, stretch=False)
        self._tree.column("name", width=160, minwidth=100)
        self._tree.column("state", width=80, minwidth=60)
        self._tree.column("entity_id", width=200, minwidth=120)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.tag_configure("on", foreground="green")
        self._tree.tag_configure("off", foreground="gray")
        self._tree.tag_configure("unavailable", foreground="red")

    def _on_refresh_click(self):
        async def _refresh():
            await self._device_manager.load_devices()
            self.refresh_tree()

        self._async_helper.run_task(_refresh())

    def _on_tree_select(self, event):
        selection = self._tree.selection()
        if selection:
            item = selection[0]
            values = self._tree.item(item, "values")
            if values and len(values) >= 4:
                entity_id = values[3]
                if entity_id and not entity_id.startswith("group_"):
                    self._on_select(entity_id)

    def refresh_tree(self):
        children = set(self._tree.get_children(""))
        existing_items: dict[str, str] = {}

        for child in children:
            values = self._tree.item(child, "values")
            if values and len(values) >= 4:
                entity_id = values[3]
                if entity_id:
                    existing_items[entity_id] = child

        devices_by_domain: dict[EntityDomain, list] = {}
        for device in self._device_manager.devices.values():
            domain = device.domain
            if domain not in devices_by_domain:
                devices_by_domain[domain] = []
            devices_by_domain[domain].append(device)

        processed_ids: set[str] = set()

        for domain in sorted(devices_by_domain.keys(), key=lambda d: d.value):
            domain_devices = sorted(
                devices_by_domain[domain], key=lambda d: d.name.lower()
            )

            group_id = f"group_{domain.value}"
            if group_id not in existing_items:
                icon = DOMAIN_ICONS.get(domain, "?")
                label = DOMAIN_LABELS.get(domain, domain.value)
                count = len(domain_devices)
                self._tree.insert(
                    "",
                    tk.END,
                    iid=group_id,
                    open=True,
                    values=(icon, f"{label} ({count})", "", ""),
                )
                existing_items[group_id] = group_id
            else:
                icon = DOMAIN_ICONS.get(domain, "?")
                label = DOMAIN_LABELS.get(domain, domain.value)
                self._tree.item(
                    group_id,
                    values=(icon, f"{label} ({len(domain_devices)})", "", ""),
                )

            for device in domain_devices:
                processed_ids.add(device.entity_id)
                if device.entity_id in existing_items:
                    self._tree.item(
                        existing_items[device.entity_id],
                        values=(
                            DOMAIN_ICONS.get(domain, "?"),
                            device.name,
                            device.state,
                            device.entity_id,
                        ),
                        tags=(self._get_state_tag(device),),
                    )
                else:
                    self._tree.insert(
                        group_id,
                        tk.END,
                        iid=device.entity_id,
                        values=(
                            DOMAIN_ICONS.get(domain, "?"),
                            device.name,
                            device.state,
                            device.entity_id,
                        ),
                        tags=(self._get_state_tag(device),),
                    )

        for entity_id, item_id in existing_items.items():
            if not entity_id.startswith("group_") and entity_id not in processed_ids:
                self._tree.delete(item_id)

    def _get_state_tag(self, device) -> str:
        if not device.is_available:
            return "unavailable"
        return "on" if device.is_on else "off"
