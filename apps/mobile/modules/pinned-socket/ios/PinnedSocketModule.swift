import ExpoModulesCore
import CryptoKit

/// Trusts the server certificate by one thing only: the SHA-256 of its leaf (the fingerprint pairing handed over).
final class PinDelegate: NSObject, URLSessionDelegate {
  private let pin: String
  init(pin: String) { self.pin = pin.lowercased().filter { $0.isHexDigit } }

  func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                  completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
    guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
          let trust = challenge.protectionSpace.serverTrust,
          let leaf = (SecTrustCopyCertificateChain(trust) as? [SecCertificate])?.first else {
      completionHandler(.cancelAuthenticationChallenge, nil); return
    }
    let der = SecCertificateCopyData(leaf) as Data
    let hex = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    if hex == pin { completionHandler(.useCredential, URLCredential(trust: trust)) }
    else { completionHandler(.cancelAuthenticationChallenge, nil) }
  }
}

private final class Conn {
  let session: URLSession
  let task: URLSessionWebSocketTask
  init(url: URL, pin: String) {
    session = URLSession(configuration: .ephemeral, delegate: PinDelegate(pin: pin), delegateQueue: nil)
    task = session.webSocketTask(with: url)
  }
}

/// A WebSocket whose certificate is checked against a pinned fingerprint. JS drives it by connection id:
/// `connect` resolves once the upgrade succeeds; text messages and the close arrive as events.
public class PinnedSocketModule: Module {
  private var conns: [String: Conn] = [:]
  private let lock = NSLock()

  private func take(_ id: String) -> Conn? { lock.lock(); defer { lock.unlock() }; return conns.removeValue(forKey: id) }
  private func get(_ id: String) -> Conn? { lock.lock(); defer { lock.unlock() }; return conns[id] }

  public func definition() -> ModuleDefinition {
    Name("PinnedSocket")
    Events("message", "close")

    AsyncFunction("connect") { (id: String, url: String, pin: String, promise: Promise) in
      guard let u = URL(string: url), u.scheme == "wss" else { promise.reject("E_URL", "a pinned connection is wss"); return }
      let c = Conn(url: u, pin: pin)
      self.lock.lock(); self.conns[id] = c; self.lock.unlock()
      c.task.resume()
      // The first ping only returns once the upgrade has succeeded, so it tells "connected" from "pin refused".
      c.task.sendPing { err in
        if let err = err { _ = self.take(id); c.task.cancel(); c.session.invalidateAndCancel(); promise.reject("E_CONNECT", err.localizedDescription); return }
        promise.resolve(nil)
        self.receive(id, c)
      }
    }

    Function("send") { (id: String, text: String) in
      self.get(id)?.task.send(.string(text)) { _ in }
    }

    Function("close") { (id: String) in
      guard let c = self.take(id) else { return }
      c.task.cancel(with: .normalClosure, reason: nil)
      c.session.invalidateAndCancel()
      self.sendEvent("close", ["id": id])
    }
  }

  private func receive(_ id: String, _ c: Conn) {
    c.task.receive { [weak self] res in
      guard let self = self, self.get(id) === c else { return }
      switch res {
      case .success(.string(let s)): self.sendEvent("message", ["id": id, "data": s]); self.receive(id, c)
      case .success(.data(let d)): self.sendEvent("message", ["id": id, "data": String(decoding: d, as: UTF8.self)]); self.receive(id, c)
      case .failure: if self.take(id) != nil { c.session.invalidateAndCancel(); self.sendEvent("close", ["id": id]) }
      @unknown default: break
      }
    }
  }
}
