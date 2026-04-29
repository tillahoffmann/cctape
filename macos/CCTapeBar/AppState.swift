import AppKit
import Foundation
import Observation

enum ConnectionState: Equatable {
	case unknown
	case connected
	case disconnected
}

@MainActor
@Observable
final class AppState {
	var connection: ConnectionState = .unknown
	var accounts: [AccountSummary] = []
	var selectedAccountId: String? {
		didSet {
			Settings.selectedAccountId = selectedAccountId
			Task { await self.refreshUsage() }
		}
	}
	var latest5hUtilization: Double?
	var latest7dUtilization: Double?
	var next5hReset: Date?
	var next7dReset: Date?
	var lastError: String?

	private let client = ProxyClient()
	private let proxy = ProxyManager.shared
	private var pollingTask: Task<Void, Never>?

	init() {
		self.selectedAccountId = Settings.selectedAccountId
	}

	var isProxyManagedByUs: Bool { proxy.didStart }

	var titleString: String {
		switch connection {
		case .disconnected, .unknown:
			return ""
		case .connected:
			if let u = latest5hUtilization {
				return "\(Int((u * 100).rounded()))%"
			} else {
				return "—"
			}
		}
	}

	var iconSystemName: String {
		switch connection {
		case .connected: return "circle.fill"
		case .disconnected, .unknown: return "circle"
		}
	}

	func start() {
		guard pollingTask == nil else { return }
		pollingTask = Task { [weak self] in
			await self?.pollLoop()
		}
	}

	private func pollLoop() async {
		var ticks = 0
		while !Task.isCancelled {
			do {
				_ = try await client.config()
				if connection != .connected {
					connection = .connected
					ticks = 0
				}
				lastError = nil
			} catch {
				connection = .disconnected
				latest5hUtilization = nil
				latest7dUtilization = nil
				next5hReset = nil
				next7dReset = nil
				try? await Task.sleep(for: .seconds(10))
				continue
			}

			if ticks % 2 == 0 {
				await refreshAccounts()
			}
			await refreshUsage()
			ticks += 1
			try? await Task.sleep(for: .seconds(30))
		}
	}

	private static let isoFractional: ISO8601DateFormatter = {
		let f = ISO8601DateFormatter()
		f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
		return f
	}()

	private static let isoPlain: ISO8601DateFormatter = {
		let f = ISO8601DateFormatter()
		f.formatOptions = [.withInternetDateTime]
		return f
	}()

	private static func parseDate(_ s: String?) -> Date? {
		guard let s else { return nil }
		return isoFractional.date(from: s) ?? isoPlain.date(from: s)
	}

	func refreshUsage() async {
		guard connection == .connected else { return }
		guard let accountId = selectedAccountId else { return }
		do {
			let records = try await client.usage(days: 1, accountId: accountId)
			let parsed: [(Date, UsageRecord)] = records.compactMap { r in
				guard let d = Self.parseDate(r.timestamp) else { return nil }
				return (d, r)
			}
			guard let latest = parsed.max(by: { $0.0 < $1.0 })?.1 else {
				latest5hUtilization = nil
				latest7dUtilization = nil
				return
			}
			latest5hUtilization = latest.unified_5h_utilization
			latest7dUtilization = latest.unified_7d_utilization

			let now = Date()
			let resets5 = parsed.compactMap { Self.parseDate($0.1.unified_5h_reset) }
			let resets7 = parsed.compactMap { Self.parseDate($0.1.unified_7d_reset) }
			next5hReset = resets5.filter { $0 > now }.min()
			next7dReset = resets7.filter { $0 > now }.min()
		} catch {
			lastError = "usage: \(error)"
		}
	}

	func refreshAccounts() async {
		guard connection == .connected else { return }
		do {
			let list = try await client.accounts()
			accounts = list
			if let id = selectedAccountId, !list.contains(where: { $0.account_id == id }) {
				selectedAccountId = list.first?.account_id
			} else if selectedAccountId == nil {
				selectedAccountId = list.first?.account_id
			}
		} catch {
			lastError = "accounts: \(error)"
		}
	}

	var selectedAccount: AccountSummary? {
		guard let id = selectedAccountId else { return nil }
		return accounts.first(where: { $0.account_id == id })
	}

	func startProxy() async {
		do {
			try proxy.start()
			for _ in 0..<30 {
				do {
					_ = try await client.config()
					connection = .connected
					await refreshAccounts()
					await refreshUsage()
					return
				} catch {
					try? await Task.sleep(for: .seconds(1))
				}
			}
			lastError = "Started uvx but proxy never became reachable."
		} catch {
			lastError = error.localizedDescription
		}
	}

	func stopProxy() async {
		await proxy.stop()
		connection = .disconnected
		latest5hUtilization = nil
		latest7dUtilization = nil
	}

	func openDashboard() {
		NSWorkspace.shared.open(Settings.baseURL)
	}

	func quit() async {
		await proxy.stop()
		NSApp.terminate(nil)
	}
}
