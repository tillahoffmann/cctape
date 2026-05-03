import Foundation

enum Settings {
	static let baseURL = URL(string: "http://127.0.0.1:5555")!

	static let selectedAccountKey = "usage.selectedAccountId"

	static var selectedAccountId: String? {
		get { UserDefaults.standard.string(forKey: selectedAccountKey) }
		set {
			if let v = newValue {
				UserDefaults.standard.set(v, forKey: selectedAccountKey)
			} else {
				UserDefaults.standard.removeObject(forKey: selectedAccountKey)
			}
		}
	}
}
