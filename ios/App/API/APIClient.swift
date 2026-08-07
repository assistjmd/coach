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

    /// Backend base URL, resolved in priority order:
    ///
    /// 1. `UserDefaults["coach.api_base_url"]` — runtime override, settable from
    ///    a scheme launch argument when pointing at a throwaway backend.
    /// 2. `CoachAPIBaseURL` in Info.plist — the build-time value, set by
    ///    `COACH_API_BASE_URL` in `App/Config.xcconfig`. This is what device
    ///    builds use, since a phone cannot reach the Mac's localhost.
    /// 3. localhost, for the simulator.
    var baseURL: URL {
        if let s = UserDefaults.standard.string(forKey: "coach.api_base_url"),
           let u = URL(string: s) { return u }
        if let s = Bundle.main.object(forInfoDictionaryKey: "CoachAPIBaseURL") as? String,
           !s.isEmpty, let u = URL(string: s) { return u }
        return URL(string: "http://localhost:8000")!
    }

    /// Parsers tried in order for any `Date` field.
    ///
    /// `.iso8601` alone is not enough: it implies `.withInternetDateTime` only,
    /// which *rejects* fractional seconds. FastAPI serialises `datetime` as
    /// `2026-08-03T13:31:00.123456+00:00`, so a field like `lastSyncedAt` decodes
    /// today only because it is still null — the first successful sync would
    /// start failing the whole `/v1/me` response.
    ///
    /// Calendar dates (`target_date`) are deliberately NOT handled here. A race
    /// day is a date, not an instant, and coercing one into a `Date` shifts it
    /// across timezones — those fields are typed `String` at the call site.
    private static let dateParsers: [(String) -> Date?] = {
        let withFractional = ISO8601DateFormatter()
        withFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return [withFractional.date(from:), plain.date(from:)]
    }()

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { decoder in
            let raw = try decoder.singleValueContainer().decode(String.self)
            for parse in APIClient.dateParsers {
                if let date = parse(raw) { return date }
            }
            throw DecodingError.dataCorrupted(
                .init(codingPath: decoder.codingPath,
                      debugDescription: "unrecognised date: \(raw)")
            )
        }
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
    func patch<R: Decodable>(_ path: String, body: [String: Any]) async throws -> R {
        try await request(path, method: "PATCH", body: body)
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

    /// Build a request URL from a path that may carry a query string.
    ///
    /// `appendingPathComponent` treats everything it is given as one path
    /// segment, so "/v1/x?a=1" became "/v1/x%3Fa%3D1" and 404'd. Nothing had a
    /// query string until the insights calendar, which is why this survived.
    func url(for path: String) throws -> URL {
        let (rawPath, query) = split(path)
        var components = URLComponents(
            url: baseURL.appendingPathComponent(rawPath), resolvingAgainstBaseURL: false
        )
        components?.percentEncodedQuery = query
        guard let built = components?.url else {
            throw APIError.http(0, "could not build URL for \(path)")
        }
        return built
    }

    private func split(_ path: String) -> (path: String, query: String?) {
        guard let mark = path.firstIndex(of: "?") else { return (path, nil) }
        return (String(path[path.startIndex..<mark]),
                String(path[path.index(after: mark)...]))
    }

    private func request<R: Decodable>(_ path: String, method: String, body: [String: Any]?) async throws -> R {
        var req = try makeRequest(path: path, method: method)
        if let body {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return try await send(req)
    }

    /// Fetch a response body without decoding it.
    ///
    /// For the data export, which is written straight to a file. Round-tripping
    /// it through a Swift type would mean maintaining a mirror of every table
    /// just to re-serialize it byte-for-byte.
    func getRaw(_ path: String) async throws -> Data {
        let req = try makeRequest(path: path, method: "GET")
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
            throw APIError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        return data
    }

    private func makeRequest(path: String, method: String) throws -> URLRequest {
        var req = URLRequest(url: try url(for: path))
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
