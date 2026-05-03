import Foundation

enum ProxyClientError: Error {
	case unreachable
	case http(Int)
	case decode(Error)
}

struct ProxyClient {
	let baseURL: URL
	let session: URLSession

	init(baseURL: URL = Settings.baseURL) {
		self.baseURL = baseURL
		let cfg = URLSessionConfiguration.ephemeral
		cfg.timeoutIntervalForRequest = 5
		cfg.timeoutIntervalForResource = 10
		cfg.waitsForConnectivity = false
		self.session = URLSession(configuration: cfg)
	}

	private func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
		var comps = URLComponents(
			url: baseURL.appendingPathComponent(path),
			resolvingAgainstBaseURL: false
		)!
		if !query.isEmpty { comps.queryItems = query }

		let (data, resp): (Data, URLResponse)
		do {
			(data, resp) = try await session.data(for: URLRequest(url: comps.url!))
		} catch {
			throw ProxyClientError.unreachable
		}
		guard let http = resp as? HTTPURLResponse else {
			throw ProxyClientError.unreachable
		}
		guard 200..<300 ~= http.statusCode else {
			throw ProxyClientError.http(http.statusCode)
		}
		do {
			return try JSONDecoder().decode(T.self, from: data)
		} catch {
			throw ProxyClientError.decode(error)
		}
	}

	func config() async throws -> AppConfig {
		try await get("/api/config")
	}

	func accounts() async throws -> [AccountSummary] {
		try await get("/api/accounts")
	}

	func sessions() async throws -> [SessionSummary] {
		try await get("/api/sessions")
	}

	func usage(days: Int, accountId: String?) async throws -> [UsageRecord] {
		var q = [URLQueryItem(name: "days", value: String(days))]
		if let a = accountId {
			q.append(URLQueryItem(name: "account_id", value: a))
		}
		return try await get("/api/usage", query: q)
	}
}
