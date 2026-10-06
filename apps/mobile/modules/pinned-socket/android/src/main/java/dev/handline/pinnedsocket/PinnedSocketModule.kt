package dev.handline.pinnedsocket

import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.ConcurrentHashMap
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/** Trusts the server certificate by one thing only: the SHA-256 of its leaf (the fingerprint pairing handed over). */
private class PinTrustManager(pin: String) : X509TrustManager {
  private val pin = pin.lowercase().filter { it in '0'..'9' || it in 'a'..'f' }

  override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
    val leaf = chain?.firstOrNull() ?: throw java.security.cert.CertificateException("no server certificate")
    val hex = MessageDigest.getInstance("SHA-256").digest(leaf.encoded).joinToString("") { "%02x".format(it) }
    if (hex != pin) throw java.security.cert.CertificateException("certificate does not match the pinned fingerprint")
  }

  override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
    throw java.security.cert.CertificateException("no client certificates")

  override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

private class Conn(val client: OkHttpClient) {
  @Volatile var socket: WebSocket? = null
}

/** A WebSocket whose certificate is checked against a pinned fingerprint; same surface as the iOS module. */
class PinnedSocketModule : Module() {
  private val conns = ConcurrentHashMap<String, Conn>()

  /** Takes the connection out of the table and frees its threads; false when someone else already did. */
  private fun release(id: String, c: Conn): Boolean {
    if (!conns.remove(id, c)) return false
    c.client.dispatcher.executorService.shutdown()
    c.client.connectionPool.evictAll()
    return true
  }

  override fun definition() = ModuleDefinition {
    Name("PinnedSocket")
    Events("message", "close")

    AsyncFunction("connect") { id: String, url: String, pin: String, promise: Promise ->
      if (!url.startsWith("wss://")) { promise.reject("E_URL", "a pinned connection is wss", null); return@AsyncFunction }
      val trust = PinTrustManager(pin)
      val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
      // The pin names the server, so the host name is not checked (the daemon is reached by address).
      val client = OkHttpClient.Builder()
        .sslSocketFactory(ssl.socketFactory, trust)
        .hostnameVerifier { _, _ -> true }
        .build()
      val c = Conn(client)
      conns[id] = c
      val opened = java.util.concurrent.atomic.AtomicBoolean(false)
      c.socket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) { opened.set(true); promise.resolve(null) }
        override fun onMessage(webSocket: WebSocket, text: String) {
          if (conns[id] === c) sendEvent("message", mapOf("id" to id, "data" to text))
        }
        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { ended(id, c) }
        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
          if (!opened.get()) { release(id, c); promise.reject("E_CONNECT", t.message ?: "connect failed", t) } else ended(id, c)
        }
      })
    }

    Function("send") { id: String, text: String -> conns[id]?.socket?.send(text); Unit }

    Function("close") { id: String ->
      val c = conns[id] ?: return@Function
      if (release(id, c)) { c.socket?.close(1000, null); sendEvent("close", mapOf("id" to id)) }
    }
  }

  private fun ended(id: String, c: Conn) { if (release(id, c)) sendEvent("close", mapOf("id" to id)) }
}
