package dev.handline.apkupdate

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import expo.modules.kotlin.Promise
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

class ApkUpdateModule : Module() {
  private val context get() = appContext.reactContext ?: throw IllegalStateException("no app context")

  override fun definition() = ModuleDefinition {
    Name("ApkUpdate")

    Function("installedVersionCode") {
      val info = context.packageManager.getPackageInfo(context.packageName, 0)
      info.longVersionCode.toInt()
    }

    Function("installedVersionName") {
      context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: ""
    }

    Function("canInstall") { context.packageManager.canRequestPackageInstalls() }

    Function("openInstallSettings") {
      val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
      intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
      context.startActivity(intent)
    }

    AsyncFunction("downloadAndInstall") { url: String, promise: Promise ->
      thread {
        try {
          require(url.startsWith("https://")) { "the update must come over https" }
          val dir = File(context.cacheDir, "updates").apply { mkdirs() }
          dir.listFiles()?.forEach { it.delete() }
          val file = File(dir, "handline.apk")
          val conn = URL(url).openConnection() as HttpURLConnection
          conn.instanceFollowRedirects = true
          conn.connectTimeout = 15000
          conn.readTimeout = 30000
          if (conn.responseCode != 200) throw IllegalStateException("download failed: ${conn.responseCode}")
          conn.inputStream.use { input -> file.outputStream().use { input.copyTo(it) } }
          val uri = FileProvider.getUriForFile(context, "${context.packageName}.apkupdate", file)
          val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
          context.startActivity(intent)
          promise.resolve(null)
        } catch (e: Exception) {
          promise.reject("E_UPDATE", e.message ?: "update failed", e)
        }
      }
    }
  }
}
