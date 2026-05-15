import Foundation

enum APIError: Error, LocalizedError {
    case http(Int, String)
    case decoding(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .http(let code, let body): return "HTTP \(code): \(body)"
        case .decoding(let e): return "Decoding: \(e.localizedDescription)"
        case .transport(let e): return "Network: \(e.localizedDescription)"
        }
    }
}

final class APIClient {
    static let shared = APIClient()

    // For dev: point at the Elastic IP (no TLS yet) until domain + cert is set up.
    // Override at runtime via UserDefaults key "coach.api_base_url" if needed.
    var baseURL: URL {
        if let s = UserDefaults.standard.string(forKey: "coach.api_base_url"),
           let u = URL(string: s) { return u }
        return URL(string: "http://localhost:8000")!
    }

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .iso8601
        return d
    }()
    private let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.dateEncodingStrategy = .iso8601
        return e
    }()

    struct AuthResponse: Decodable {
        let accessToken: String
        let userId: String
    }

    @discardableResult
    func post<R: Decodable>(_ path: String, body: [String: Any]? = nil) async throws -> R {
        try await request(path, method: "POST", body: body)
    }

    @discardableResult
    func get<R: Decodable>(_ path: String) async throws -> R {
        try await request(path, method: "GET", body: nil)
    }

    @discardableResult
    func put<R: Decodable>(_ path: String, body: [String: Any]) async throws -> R {
        try await request(path, method: "PUT", body: body)
    }

    @discardableResult
    func delete(_ path: String) async throws -> Void {
        let _: EmptyResponse = try await request(path, method: "DELETE", body: nil)
    }

    func postJSON<B: Encodable, R: Decodable>(_ path: String, body: B) async throws -> R {
        var req = try makeRequest(path: path, method: "POST")
        req.httpBody = try encoder.encode(body)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return try await send(req)
    }

    private struct EmptyResponse: Decodable {}

    private func request<R: Decodable>(_ path: String, method: String, body: [String: Any]?) async throws -> R {
        var req = try makeRequest(path: path, method: method)
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return try await send(req)
    }

    private func makeRequest(path: String, method: String) throws -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = method
        if let token = Keychain.read("coach.access_token") {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return req
    }

    private func send<R: Decodable>(_ req: URLRequest) async throws -> R {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.http(0, "no response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.http(http.statusCode, body)
        }
        if data.isEmpty { return try decoder.decode(R.self, from: "{}".data(using: .utf8)!) }
        do { return try decoder.decode(R.self, from: data) }
        catch { throw APIError.decoding(error) }
    }
}
