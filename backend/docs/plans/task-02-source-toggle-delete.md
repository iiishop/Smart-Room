# Task 02 — Source Toggle + Delete

## Scope
每个已添加的源卡片上添加启用开关（QCheckBox）和删除按钮（×），支持选择性启动/停止，删除后网格自动重排。

## Files to modify
- `discover_client/gui/main_window.py` — SourcePanel + MainWindow

## Task 2.1 — SourcePanel: add toggle checkbox + delete button

在 SourcePanel 的标题栏中加一个 QCheckBox 和一个删除按钮：

```python
class SourcePanel(QGroupBox):
    edit_requested = Signal(str, str, dict)
    source_toggled = Signal(str, bool)        # 新增：source_id, enabled
    source_delete_requested = Signal(str)      # 新增：source_id

    def __init__(self, source_id, source_type, settings, parent=None):
        ...
        self._enabled = True  # 默认启用

        # Title bar 改造
        title_bar = QHBoxLayout()
        # Checkbox (启用/禁用)
        self._checkbox = QCheckBox()
        self._checkbox.setChecked(True)
        self._checkbox.toggled.connect(self._on_toggle)
        title_bar.addWidget(self._checkbox)

        self._dot = QLabel("○")
        ...

        title_bar.addStretch()
        # Stats label
        ...

        # 删除按钮
        self._del_btn = QPushButton("×")
        self._del_btn.setFixedSize(22, 22)
        self._del_btn.setObjectName("btnDelete")
        self._del_btn.clicked.connect(lambda: self.source_delete_requested.emit(self.source_id))
        title_bar.addWidget(self._del_btn)

    def _on_toggle(self, checked: bool) -> None:
        self._enabled = checked
        self.source_toggled.emit(self.source_id, checked)

    @property
    def enabled(self) -> bool:
        return self._enabled
```

QSS 中加 `#btnDelete` 样式：深灰背景、无边框、白色文字、hover 变红。

## Task 2.2 — MainWindow: 连接新信号 + 重排网格

```python
class MainWindow:
    def _add_panel(self, source_id, source_type, settings):
        ...
        panel.source_toggled.connect(self._on_toggle_source)
        panel.source_delete_requested.connect(self._on_delete_source)
        ...

    def _on_toggle_source(self, source_id: str, enabled: bool) -> None:
        self._save_config()

    def _on_delete_source(self, source_id: str) -> None:
        panel = self._panels.pop(source_id, None)
        if panel is None:
            return
        self._card_grid.removeWidget(panel)
        panel.deleteLater()
        self._reflow_cards()
        self._save_config()

    def _reflow_cards(self) -> None:
        """Remove all cards from grid and re-add in order to fill gaps."""
        panels = list(self._panels.items())
        for _, panel in panels:
            self._card_grid.removeWidget(panel)
        for i, (_, panel) in enumerate(panels):
            self._card_grid.addWidget(panel, i // 4, i % 4)

    def _save_config(self) -> None:
        ...
        for sid, panel in self._panels.items():
            lines.append(f'\n[[sources]]')
            lines.append(f'source_id = "{sid}"')
            lines.append(f'source_type = "{panel.source_type}"')
            lines.append(f'enabled = {str(panel.enabled).lower()}\n')  # ← 改为真实值
            ...
```

## Task 2.3 — QSS: 删除按钮样式

在 `style.qss` 的 Buttons 段后面加：

```css
#btnDelete {
    background-color: transparent;
    color: rgb(120, 125, 145);
    border: none;
    font-size: 12pt;
    font-weight: bold;
    padding: 0px;
    border-radius: 4px;
}
#btnDelete:hover {
    background-color: rgb(60, 50, 50);
    color: rgb(255, 90, 90);
}
```

## Verification

```python
# 1. SourcePanel has checkbox and delete button
panel = SourcePanel("test", "mqtt", {})
assert panel.enabled is True
panel._checkbox.setChecked(False)
assert panel.enabled is False

# 2. Delete removes panel and reflows grid
# (manual test: add 3 sources, delete middle one, check remaining 2 sit at positions 0,0 and 0,1)

# 3. Config saves enabled=false for toggled-off source
# (manual test: toggle source off, check config.toml has enabled = false)
```
