import SwiftUI

struct PopoverView: View {
	@Bindable var state: AppState

	var body: some View {
		VStack(alignment: .leading, spacing: 12) {
			header
			Divider()
			if state.connection == .connected {
				if state.accounts.isEmpty {
					Text("No accounts recorded yet.")
						.font(.callout)
						.foregroundStyle(.secondary)
				} else {
					bars
					Divider()
					accountSummary
				}
			} else {
				disconnectedView
			}
			Divider()
			actions
		}
		.padding(14)
		.frame(width: 320)
	}

	@ViewBuilder
	private var header: some View {
		HStack {
			if state.accounts.isEmpty {
				Text("cctape")
					.font(.headline)
			} else {
				Picker(
					"",
					selection: Binding(
						get: { state.selectedAccountId ?? "" },
						set: { state.selectedAccountId = $0.isEmpty ? nil : $0 }
					)
				) {
					ForEach(state.accounts) { a in
						Text(displayId(a.account_id)).tag(a.account_id)
					}
				}
				.labelsHidden()
				.pickerStyle(.menu)
			}
			Spacer()
			statusDot
		}
	}

	private var statusDot: some View {
		Circle()
			.fill(state.connection == .connected ? Color.green : Color.secondary.opacity(0.4))
			.frame(width: 8, height: 8)
	}

	private var bars: some View {
		VStack(alignment: .leading, spacing: 10) {
			usageBar(
				label: "5-hour window",
				value: state.latest5hUtilization,
				resetAt: state.next5hReset
			)
			usageBar(
				label: "7-day window",
				value: state.latest7dUtilization,
				resetAt: state.next7dReset
			)
		}
	}

	private func usageBar(label: String, value: Double?, resetAt: Date?) -> some View {
		VStack(alignment: .leading, spacing: 4) {
			HStack {
				Text(label)
					.font(.caption)
					.foregroundStyle(.secondary)
				Spacer()
				Text(value.map { "\(Int(($0 * 100).rounded()))%" } ?? "—")
					.font(.caption)
					.monospacedDigit()
			}
			ProgressView(value: min(max(value ?? 0, 0), 1))
				.progressViewStyle(.linear)
				.tint(barColor(value ?? 0))
			if let r = resetAt {
				Text("Resets \(formatRelative(r)) (\(formatAbsolute(r)))")
					.font(.caption2)
					.foregroundStyle(.secondary)
			}
		}
	}

	private func barColor(_ v: Double) -> Color {
		switch v {
		case ..<0.5: return .green
		case ..<0.8: return .yellow
		default: return .red
		}
	}

	@ViewBuilder
	private var accountSummary: some View {
		if let acc = state.selectedAccount {
			VStack(alignment: .leading, spacing: 4) {
				row("Cumulative cost", formatCost(acc.cost_usd))
				row("Messages", formatInt(acc.message_count))
			}
			.font(.caption)
		}
	}

	private func row(_ label: String, _ value: String) -> some View {
		HStack {
			Text(label).foregroundStyle(.secondary)
			Spacer()
			Text(value).monospacedDigit()
		}
	}

	private var disconnectedView: some View {
		VStack(alignment: .leading, spacing: 6) {
			Text("Proxy not running")
				.font(.subheadline)
			Text("Start cctape to begin tracking usage.")
				.font(.caption)
				.foregroundStyle(.secondary)
			if let err = state.lastError {
				Text(err)
					.font(.caption2)
					.foregroundStyle(.red)
					.lineLimit(3)
			}
		}
	}

	private var actions: some View {
		VStack(alignment: .leading, spacing: 4) {
			actionButton(title: "Open Dashboard", systemImage: "safari") {
				state.openDashboard()
			}

			if state.connection == .connected && state.isProxyManagedByUs {
				actionButton(title: "Stop proxy", systemImage: "stop.circle") {
					Task { await state.stopProxy() }
				}
			} else if state.connection != .connected {
				actionButton(title: "Start proxy", systemImage: "play.circle") {
					Task { await state.startProxy() }
				}
			}

			Divider().padding(.vertical, 2)

			actionButton(title: "Quit cctape bar", systemImage: "power", shortcut: "q") {
				Task { await state.quit() }
			}
		}
	}

	private func actionButton(
		title: String,
		systemImage: String,
		shortcut: KeyEquivalent? = nil,
		action: @escaping () -> Void
	) -> some View {
		let button = Button(action: action) {
			Label(title, systemImage: systemImage)
				.frame(maxWidth: .infinity, alignment: .leading)
				.contentShape(Rectangle())
		}
		.buttonStyle(.borderless)
		.font(.callout)
		return Group {
			if let s = shortcut {
				button.keyboardShortcut(s)
			} else {
				button
			}
		}
	}

	private func displayId(_ id: String) -> String {
		if let dash = id.firstIndex(of: "-") {
			return String(id[id.startIndex..<dash]) + "…"
		}
		return String(id.prefix(8)) + "…"
	}

	private func formatCost(_ v: Double?) -> String {
		guard let v else { return "—" }
		if v < 0.01 { return "<$0.01" }
		return String(format: "$%.2f", v)
	}

	private func formatInt(_ n: Int) -> String {
		let f = NumberFormatter()
		f.numberStyle = .decimal
		return f.string(from: NSNumber(value: n)) ?? String(n)
	}

	private func formatRelative(_ d: Date) -> String {
		let f = RelativeDateTimeFormatter()
		f.unitsStyle = .abbreviated
		return f.localizedString(for: d, relativeTo: Date())
	}

	private func formatAbsolute(_ d: Date) -> String {
		let f = DateFormatter()
		f.timeStyle = .short
		if d.timeIntervalSinceNow > 12 * 3600 {
			f.dateStyle = .short
		} else {
			f.dateStyle = .none
		}
		return f.string(from: d)
	}
}
