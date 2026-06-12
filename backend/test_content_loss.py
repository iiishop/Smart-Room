"""Reproduce QLineEdit content loss on DeviceProfileCard.update()."""
import sys
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
from discover_client.gui.device_profile_page import DeviceProfileCard, DeviceProfile

app = QApplication(sys.argv)


def find_qlineedit(widget):
    if isinstance(widget, QLineEdit):
        return widget
    layout = getattr(widget, 'layout', None)
    if callable(layout):
        layout = layout()
    if layout:
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            if item.widget():
                result = find_qlineedit(item.widget())
                if result:
                    return result
            if item.layout():
                result = find_qlineedit_in_layout(item.layout())
                if result:
                    return result
    return None


def find_qlineedit_in_layout(layout):
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        if item.widget():
            if isinstance(item.widget(), QLineEdit):
                return item.widget()
            result = find_qlineedit(item.widget())
            if result:
                return result
        if item.layout():
            result = find_qlineedit_in_layout(item.layout())
            if result:
                return result
    return None


# Simulate what worker.py _build_device_profiles produces
profile1 = DeviceProfile(
    device_id="light-1",
    category="command",
    confidence=0.85,
    ip_addresses={"192.168.1.10"},
    mac_prefixes={"aa:bb:cc"},
    operations=[
        {
            "action": "Set",
            "topic": "home/light/set",
            "accepted_values": [],
            "args": [{"key": "value", "type": "string", "example": ""}],
        }
    ],
)

card = DeviceProfileCard(profile1)
card.set_expanded(True)
card.show()

print(f"Card._operation_rows: {list(card._operation_rows.keys())}")
print(f"Operations layout count: {card._operations_layout.count()}")
if card._operations_layout.count() > 0:
    item0 = card._operations_layout.itemAt(0)
    if item0:
        w = item0.widget()
        if w:
            print(f"  First widget type: {type(w).__name__}")
            if hasattr(w, 'text'):
                print(f"  text: '{w.text()}'")

qle = find_qlineedit(card)
if qle:
    qle.setText("hello world")
    print(f"BEFORE refresh: QLineEdit text = '{qle.text()}', hasFocus = {qle.hasFocus()}")
else:
    print("BEFORE refresh: NO QLineEdit found!")
    sys.exit(1)

# Simulate a data refresh — accepted_values changed (args changed from 1 entry to empty)
profile2 = DeviceProfile(
    device_id="light-1",
    category="command",
    confidence=0.90,
    ip_addresses={"192.168.1.10"},
    mac_prefixes={"aa:bb:cc"},
    operations=[
        {
            "action": "Set",
            "topic": "home/light/set",
            "accepted_values": ["ON"],
            "args": [],
        }
    ],
)

card.update(profile2)

qle2 = find_qlineedit(card)
if qle2:
    print(f"AFTER refresh: QLineEdit text = '{qle2.text()}', hasFocus = {qle2.hasFocus()}")
    print(f"Same object: {qle2 is qle}")
else:
    print("AFTER refresh: QLineEdit GONE — content lost!")
    print(f"_operation_rows keys: {list(card._operation_rows.keys())}")
    for key, row in card._operation_rows.items():
        print(f"  Row key={key}")
        layout = row.layout()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    text = w.text() if hasattr(w, 'text') else 'N/A'
                    print(f"    widget: {type(w).__name__} text='{text}'")
