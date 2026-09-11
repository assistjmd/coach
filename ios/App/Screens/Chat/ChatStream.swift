import Foundation

enum ChatStream {
    static func stream(message: String) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var req = URLRequest(
                        url: APIClient.shared.baseURL.appendingPathComponent("/v1/chat")
                    )
                    req.httpMethod = "POST"
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    req.setValue("2", forHTTPHeaderField: "X-Coach-Stream-Version")
                    if let token = Keychain.read("coach.access_token") {
                        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                    }
                    req.httpBody = try JSONSerialization.data(
                        withJSONObject: ["message": message]
                    )

                    let (bytes, response) = try await URLSession.shared.bytes(for: req)
                    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                    guard (200..<300).contains(status) else {
                        // Read the body before throwing. Discarding it turned a
                        // rate-limit response — which carries a sentence the user
                        // needs — into a bare "stream error".
                        var raw = ""
                        for try await line in bytes.lines { raw += line }
                        throw APIError.http(status, Self.detail(from: raw))
                    }
                    for try await line in bytes.lines {
                        guard line.hasPrefix("data: ") else { continue }
                        let payload = String(line.dropFirst(6))
                        if payload == "[DONE]" { break }
                        let event = try JSONDecoder().decode(Event.self, from: Data(payload.utf8))
                        if let error = event.error { throw APIError.http(502, error) }
                        if let delta = event.delta { continuation.yield(delta) }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private struct Event: Decodable {
        let delta: String?
        let error: String?
    }

    /// FastAPI wraps errors as `{"detail": "..."}`. Pull the sentence out so it
    /// can be shown as-is; fall back to the raw body if the shape is unexpected.
    private static func detail(from raw: String) -> String {
        guard let data = raw.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let detail = object["detail"] as? String
        else { return raw }
        return detail
    }
}
