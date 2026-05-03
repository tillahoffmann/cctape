import Foundation

struct AppConfig: Decodable {
	let version: String
	let db_path: String?
	let anthropic_base_url: String?
}

struct AccountSummary: Decodable, Identifiable, Hashable {
	let account_id: String
	let message_count: Int
	let input_tokens: Int?
	let output_tokens: Int?
	let cache_creation_input_tokens: Int?
	let cache_read_input_tokens: Int?
	let cost_usd: Double?
	let first_timestamp: String
	let last_timestamp: String

	var id: String { account_id }
}

struct SessionSummary: Decodable, Identifiable, Hashable {
	let session_id: String
	let last_timestamp: String
	let turn_count: Int
	let cost_usd: Double?
	let first_message_preview: String?
	let title: String?

	var id: String { session_id }

	var displayName: String {
		if let t = title, !t.isEmpty { return t }
		if let p = first_message_preview, !p.isEmpty { return String(p.prefix(40)) }
		return String(session_id.prefix(8))
	}
}

struct UsageRecord: Decodable {
	let timestamp: String
	let unified_5h_utilization: Double?
	let unified_7d_utilization: Double?
	let unified_5h_reset: String?
	let unified_7d_reset: String?
}
