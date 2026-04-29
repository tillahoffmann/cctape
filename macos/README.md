# cctape bar

A native macOS menu bar app that surfaces cctape's usage stats at a glance.

- Shows current 5-hour Claude utilization (`● 47%`) in the menu bar.
- Click for 5h/7d bars, next-reset countdowns, cumulative cost, and an "Open Dashboard" button.
- Can start/stop a local `uvx cctape` process if one isn't already running.

## Requirements

- macOS 14 (Sonoma) or later
- Xcode 15+
- [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`)
- [`uv` / `uvx`](https://docs.astral.sh/uv/) on `PATH` (only needed if you want the app to launch cctape itself)

## Build & run

```sh
cd macos
xcodegen          # generates CCTapeBar.xcodeproj from project.yml
open CCTapeBar.xcodeproj
# In Xcode: select the CCTapeBar scheme and hit Run (⌘R).
```

A circle icon appears in the menu bar. Click it to open the popover.

If cctape is already running (`uvx cctape` in another terminal, or via this app's "Start proxy" button), the icon turns filled and the title shows the current 5h utilization. Otherwise it stays hollow.

## Configuration

The app talks to `http://127.0.0.1:5555` (cctape's default). To point at a non-default port, edit `Settings.baseURL` in `CCTapeBar/Settings.swift`.

The selected account is persisted to `UserDefaults` under `usage.selectedAccountId` — the same key the web UI uses, so the two stay in sync.
