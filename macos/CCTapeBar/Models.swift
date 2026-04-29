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

struct UsageRecord: Decodable {
	let timestamp: String
	let unified_5h_utilization: Double?
	let unified_7d_utilization: Double?
	let unified_5h_reset: String?
	let unified_7d_reset: String?
}
