package com.reelrelay.app

import java.util.Locale

/**
 * Turns yt-dlp's verbose failures into something the user can act on.
 *
 * Instagram and Facebook are the usual sources of "login required" errors, so
 * the advice mentions cookies whenever the app has no cookie jar imported.
 */
internal fun friendlyError(raw: String?, hasCookies: Boolean): String? {
    val message = raw?.trim().orEmpty()
    if (message.isEmpty()) return null
    val text = message.lowercase(Locale.US)
    val last = message.lineSequence().lastOrNull { it.isNotBlank() }?.trim() ?: message

    return when {
        text.contains("unsupported url") || text.contains("is not a valid url") ->
            "That link is not supported. Paste a YouTube, Instagram or Facebook video, Reel or Short link."

        text.contains("http error 429") || text.contains("too many requests") ||
            text.contains("rate-limit") || text.contains("rate limit") ->
            "The site is rate limiting this connection. Wait a few minutes and try again."

        text.contains("private") && (text.contains("video") || text.contains("account") || text.contains("post")) ->
            "This post is private: only followers with access can download it. " + loginHint(hasCookies)

        text.contains("login required") || text.contains("log in") ||
            text.contains("sign in") || text.contains("requires authentication") ||
            text.contains("cookies") || text.contains("checkpoint") ->
            "The site wants a logged-in session for this link. " + loginHint(hasCookies)

        text.contains("age") && text.contains("restrict") ->
            "This video is age-restricted. " + loginHint(hasCookies)

        text.contains("not available in your country") || text.contains("geo") ||
            text.contains("blocked in your country") ->
            "This video is not available in your region."

        text.contains("http error 403") || text.contains("forbidden") ->
            "The media server refused the request (HTTP 403). It may be an expired or region-locked link. " +
                loginHint(hasCookies)

        text.contains("http error 404") || text.contains("not found") ->
            "The post was not found. It may have been deleted or the link is incomplete."

        text.contains("is live") || text.contains("live event") || text.contains("premieres") ->
            "Live streams and upcoming premieres cannot be downloaded. Try again after the broadcast ends."

        text.contains("no downloadable formats") || text.contains("requested format is not available") ->
            "The site did not expose a downloadable format for this link."

        text.contains("unable to download video data") || text.contains("unable to download") ->
            "The media download failed. " + loginHint(hasCookies)

        text.contains("timed out") || text.contains("timeout") || text.contains("connection") ->
            "The connection to the site failed. Check your network and try again."

        else -> last.take(300)
    }
}

private fun loginHint(hasCookies: Boolean): String =
    if (hasCookies) {
        "Your imported cookies may have expired — export cookies.txt again from a logged-in browser."
    } else {
        "Import a cookies.txt exported from a logged-in browser to download it."
    }
