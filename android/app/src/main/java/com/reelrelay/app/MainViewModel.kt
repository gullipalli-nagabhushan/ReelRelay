package com.reelrelay.app

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

data class MainUiState(
    val engineReady: Boolean = false,
    val engineError: String? = null,
    val loadingInfo: Boolean = false,
    val info: MediaSummary? = null,
    val choices: List<FormatChoice> = emptyList(),
    val selectedIndex: Int = 0,
    val downloading: Boolean = false,
    val progress: Int = 0,
    val indeterminate: Boolean = true,
    val status: String = "",
    val error: String? = null,
    val savedName: String? = null,
    val cookiesLoaded: Boolean = false,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val app = application as ReelRelayApp
    private val repository = DownloadRepository(application)
    private val _state = MutableStateFlow(
        MainUiState(cookiesLoaded = CookieStore.hasCookies(application)),
    )
    val state: StateFlow<MainUiState> = _state.asStateFlow()

    private var downloadJob: Job? = null
    private var currentProcessId: String? = null

    init {
        viewModelScope.launch {
            val result = app.awaitEngine()
            _state.update {
                it.copy(
                    engineReady = result.isSuccess,
                    engineError = result.exceptionOrNull()?.let { error ->
                        friendlyError(error.message, CookieStore.hasCookies(app))
                    },
                )
            }
        }
    }

    fun extract(rawUrl: String) {
        val url = rawUrl.trim()
        if (url.isBlank()) {
            _state.update { it.copy(error = string(R.string.empty_url)) }
            return
        }
        viewModelScope.launch {
            _state.update {
                it.copy(
                    loadingInfo = true,
                    error = null,
                    info = null,
                    choices = emptyList(),
                    status = "",
                    savedName = null,
                )
            }
            try {
                app.awaitEngine().getOrThrow()
                val cookies = cookiesPath()
                val info = withContext(Dispatchers.IO) {
                    if (cookies == null) {
                        YoutubeDL.getInstance().getInfo(url)
                    } else {
                        val request = YoutubeDLRequest(url).apply {
                            addOption("--cookies", cookies)
                        }
                        YoutubeDL.getInstance().getInfo(request)
                    }
                }
                val summary = buildSummary(info)
                _state.update {
                    it.copy(
                        loadingInfo = false,
                        info = summary,
                        choices = summary.choices,
                        selectedIndex = 0,
                        error = if (summary.choices.isEmpty()) {
                            "No downloadable formats were found for this link."
                        } else {
                            null
                        },
                    )
                }
            } catch (t: Throwable) {
                _state.update {
                    it.copy(
                        loadingInfo = false,
                        error = friendlyError(t.message, CookieStore.hasCookies(app))
                            ?: "The link could not be read.",
                    )
                }
            }
        }
    }

    fun select(index: Int) {
        _state.update { it.copy(selectedIndex = index) }
    }

    fun importCookies(uri: Uri) {
        viewModelScope.launch {
            val count = withContext(Dispatchers.IO) {
                runCatching { CookieStore.import(app, uri) }.getOrNull()
            }
            if (count == null) {
                _state.update {
                    it.copy(
                        error = "That file is not a Netscape cookies.txt. Export cookies from a " +
                            "logged-in browser (for example with the \"Get cookies.txt\" extension).",
                    )
                }
            } else {
                _state.update {
                    it.copy(
                        cookiesLoaded = true,
                        error = null,
                        status = "Cookies imported — $count entries. Instagram and Facebook links " +
                            "can now use your session.",
                    )
                }
            }
        }
    }

    fun clearCookies() {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { CookieStore.clear(app) }
            _state.update {
                it.copy(cookiesLoaded = false, status = "Cookies removed.", error = null)
            }
        }
    }

    fun dismissMessage() {
        _state.update { it.copy(error = null, status = "") }
    }

    fun startDownload() {
        val snapshot = _state.value
        val info = snapshot.info ?: return
        val url = info.webpageUrl.ifBlank { return }
        val choice = snapshot.choices.getOrNull(snapshot.selectedIndex) ?: return
        if (snapshot.downloading) return

        val processId = "reelrelay-${System.currentTimeMillis()}"
        currentProcessId = processId
        downloadJob = viewModelScope.launch {
            _state.update {
                it.copy(
                    downloading = true,
                    progress = 0,
                    indeterminate = true,
                    status = string(R.string.downloading),
                    error = null,
                    savedName = null,
                )
            }
            var produced: File? = null
            try {
                val cookies = cookiesPath()
                val file = withContext(Dispatchers.IO) {
                    repository.download(url, info.title, choice, processId, cookies) { progress, eta, line ->
                        _state.update {
                            it.copy(
                                progress = progress.coerceIn(0f, 100f).toInt(),
                                indeterminate = false,
                                status = progressLine(progress, eta, line),
                            )
                        }
                    }
                }
                produced = file
                _state.update { it.copy(indeterminate = true, status = "Saving to Downloads/ReelRelay…") }
                val uri = withContext(Dispatchers.IO) { MediaPublisher.publish(app, file) }
                _state.update {
                    it.copy(
                        downloading = false,
                        progress = 100,
                        indeterminate = false,
                        status = if (uri != null) {
                            "Saved to Downloads/ReelRelay — ${file.name}"
                        } else {
                            "The file could not be saved."
                        },
                        savedName = if (uri != null) file.name else null,
                    )
                }
            } catch (cancel: CancellationException) {
                throw cancel
            } catch (t: Throwable) {
                val message = if (t is YoutubeDL.CanceledException) {
                    "Download cancelled."
                } else {
                    friendlyError(t.message, CookieStore.hasCookies(app))
                        ?: "The download failed."
                }
                _state.update {
                    it.copy(downloading = false, indeterminate = false, status = "", error = message)
                }
            } finally {
                produced?.parentFile?.deleteRecursively()
                currentProcessId = null
            }
        }
    }

    fun cancelDownload() {
        currentProcessId?.let { YoutubeDL.getInstance().destroyProcessById(it) }
        downloadJob?.cancel()
        _state.update {
            it.copy(
                downloading = false,
                indeterminate = false,
                status = "",
                error = "Download cancelled.",
            )
        }
    }

    private fun cookiesPath(): String? =
        if (CookieStore.hasCookies(app)) CookieStore.file(app).absolutePath else null

    private fun progressLine(progress: Float, eta: Long, line: String): String {
        val percent = progress.coerceIn(0f, 100f).toInt()
        val etaText = when {
            eta <= 0 -> ""
            eta >= 60 -> " · ${eta / 60}m ${eta % 60}s left"
            else -> " · ${eta}s left"
        }
        val detail = line.lineSequence().lastOrNull { it.isNotBlank() }?.take(160).orEmpty()
        return if (percent > 0) {
            "Downloading $percent%$etaText"
        } else {
            detail.ifBlank { "Contacting the source…" }
        }
    }

    private fun string(id: Int): String = getApplication<Application>().getString(id)
}
