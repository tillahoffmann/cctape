import SwiftUI

@main
struct CCTapeBarApp: App {
	@NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
	@State private var state: AppState

	init() {
		let s = AppState()
		_state = State(initialValue: s)
		s.start()
	}

	var body: some Scene {
		MenuBarExtra {
			PopoverView(state: state)
		} label: {
			HStack(spacing: 4) {
				Image(systemName: state.iconSystemName)
				if !state.titleString.isEmpty {
					Text(state.titleString)
						.monospacedDigit()
				}
			}
		}
		.menuBarExtraStyle(.window)
	}
}

final class AppDelegate: NSObject, NSApplicationDelegate {
	func applicationWillTerminate(_ notification: Notification) {
		MainActor.assumeIsolated {
			ProxyManager.shared.stopSync()
		}
	}
}
