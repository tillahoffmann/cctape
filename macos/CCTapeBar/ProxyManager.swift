import Foundation

@MainActor
final class ProxyManager {
	static let shared = ProxyManager()

	private var process: Process?
	private(set) var didStart = false
	private var cachedUvxPath: String?

	var isRunning: Bool { process?.isRunning ?? false }

	/// GUI apps don't inherit the user's shell PATH, so probe well-known
	/// install locations and then fall back to a login shell lookup.
	private func locateUvx() -> String? {
		if let cached = cachedUvxPath { return cached }

		let candidates = [
			"/opt/homebrew/bin/uvx",
			"/usr/local/bin/uvx",
			"\(NSHomeDirectory())/.local/bin/uvx",
		]
		for path in candidates {
			if FileManager.default.isExecutableFile(atPath: path) {
				cachedUvxPath = path
				return path
			}
		}

		let p = Process()
		p.executableURL = URL(fileURLWithPath: "/bin/bash")
		p.arguments = ["-lc", "command -v uvx"]
		let stdout = Pipe()
		p.standardOutput = stdout
		p.standardError = Pipe()
		do {
			try p.run()
			p.waitUntilExit()
			guard p.terminationStatus == 0 else { return nil }
			let data = stdout.fileHandleForReading.readDataToEndOfFile()
			let path = String(data: data, encoding: .utf8)?
				.trimmingCharacters(in: .whitespacesAndNewlines)
			if let path, !path.isEmpty, FileManager.default.isExecutableFile(atPath: path) {
				cachedUvxPath = path
				return path
			}
		} catch {
			return nil
		}
		return nil
	}

	func start() throws {
		guard process == nil else { return }
		guard let uvx = locateUvx() else {
			throw NSError(
				domain: "ProxyManager", code: 1,
				userInfo: [
					NSLocalizedDescriptionKey:
						"Could not find uvx. Install uv (brew install uv) or start cctape manually."
				])
		}
		let p = Process()
		p.executableURL = URL(fileURLWithPath: uvx)
		p.arguments = ["cctape", "--no-browser"]
		p.standardOutput = Pipe()
		p.standardError = Pipe()
		try p.run()
		process = p
		didStart = true
	}

	func stop() async {
		guard let p = process else { return }
		if p.isRunning {
			p.terminate()
			for _ in 0..<30 {
				if !p.isRunning { break }
				try? await Task.sleep(for: .milliseconds(100))
			}
			if p.isRunning {
				kill(p.processIdentifier, SIGKILL)
			}
		}
		process = nil
		didStart = false
	}

	/// Synchronous variant for `applicationWillTerminate`, where we can't await.
	func stopSync() {
		guard let p = process else { return }
		if p.isRunning {
			p.terminate()
			let deadline = Date().addingTimeInterval(3)
			while p.isRunning && Date() < deadline {
				Thread.sleep(forTimeInterval: 0.05)
			}
			if p.isRunning {
				kill(p.processIdentifier, SIGKILL)
			}
		}
		process = nil
		didStart = false
	}
}
