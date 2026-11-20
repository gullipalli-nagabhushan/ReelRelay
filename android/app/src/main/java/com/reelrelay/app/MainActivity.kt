package com.reelrelay.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.ArrayAdapter
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import coil.load
import com.google.android.material.snackbar.Snackbar
import com.reelrelay.app.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val viewModel: MainViewModel by viewModels()

    private var player: ExoPlayer? = null
    private var pendingDownload = false
    private var lastPreview: PreviewSource? = null
    private var lastChoices: List<FormatChoice> = emptyList()
    private var lastError: String? = null

    private val storagePermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val wantedDownload = pendingDownload
            pendingDownload = false
            if (granted && wantedDownload) {
                viewModel.startDownload()
            } else if (!granted) {
                Snackbar.make(binding.root, R.string.storage_permission, Snackbar.LENGTH_LONG).show()
            }
        }

    private val openCookies =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) viewModel.importCookies(uri)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.previewButton.setOnClickListener { submit() }
        binding.downloadButton.setOnClickListener { onDownloadClicked() }
        binding.cancelButton.setOnClickListener { viewModel.cancelDownload() }
        binding.cookiesButton.setOnClickListener {
            openCookies.launch(arrayOf("text/plain", "text/*", "application/octet-stream", "*/*"))
        }
        binding.cookiesClearButton.setOnClickListener { viewModel.clearCookies() }
        binding.urlInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_GO || actionId == EditorInfo.IME_ACTION_DONE) {
                submit()
                true
            } else {
                false
            }
        }
        binding.formatInput.setOnItemClickListener { _, _, position, _ ->
            viewModel.select(position)
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.state.collect(::render)
            }
        }

        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun submit() {
        hideKeyboard()
        viewModel.extract(binding.urlInput.text?.toString().orEmpty())
    }

    private fun onDownloadClicked() {
        val needsLegacyPermission = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.WRITE_EXTERNAL_STORAGE,
            ) != PackageManager.PERMISSION_GRANTED
        if (needsLegacyPermission) {
            pendingDownload = true
            storagePermission.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        } else {
            viewModel.startDownload()
        }
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND || intent.type != "text/plain") return
        val shared = intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
        val url = Regex("https?://\\S+").find(shared)?.value ?: shared.trim()
        if (url.startsWith("http://") || url.startsWith("https://")) {
            binding.urlInput.setText(url)
            viewModel.extract(url)
        }
    }

    private fun render(state: MainUiState) {
        binding.loadingBar.isVisible = state.loadingInfo
        binding.previewButton.isEnabled = !state.loadingInfo && !state.downloading

        val engineText = when {
            state.engineError != null -> "Engine error: ${state.engineError}"
            state.engineReady -> "On-device engine ready — yt-dlp + ffmpeg"
            else -> getString(R.string.startup)
        }
        binding.engineStatus.text = engineText
        binding.engineStatus.setTextColor(
            ContextCompat.getColor(
                this,
                if (state.engineError != null) R.color.rr_rose else R.color.rr_cyan,
            )
        )

        binding.cookiesStatus.text = getString(
            if (state.cookiesLoaded) R.string.cookies_loaded else R.string.cookies_missing,
        )
        binding.cookiesClearButton.isVisible = state.cookiesLoaded
        binding.cookiesButton.isEnabled = !state.downloading

        val info = state.info
        binding.mediaCard.isVisible = info != null
        if (info != null) {
            binding.titleText.text = info.title
            binding.uploaderText.text = info.uploader
            binding.uploaderText.isVisible = info.uploader.isNotBlank()
            binding.metaText.text = info.durationLabel
            binding.metaText.isVisible = info.durationLabel.isNotBlank()
            binding.thumbnail.load(info.thumbnail)

            val playlist = state.playlist
            binding.playlistText.isVisible = playlist != null
            if (playlist != null) {
                binding.playlistText.text = playlistPreview(playlist)
            }

            if (playlist != null) {
                lastPreview = null
                releasePlayer()
                binding.playerView.isVisible = false
                binding.previewUnavailable.isVisible = false
            } else if (info.preview != lastPreview) {
                lastPreview = info.preview
                showPreview(info.preview)
            }
        } else if (lastPreview != null) {
            lastPreview = null
            releasePlayer()
            binding.playerView.isVisible = false
            binding.previewUnavailable.isVisible = false
        }

        updateChoices(state.choices, state.selectedIndex)
        binding.formatLayout.isVisible = state.choices.isNotEmpty()
        binding.downloadButton.isVisible = state.choices.isNotEmpty() && !state.downloading
        binding.downloadButton.text = state.playlist?.let { listing ->
            getString(R.string.playlist_download, listing.entries.size)
        } ?: getString(R.string.download)
        binding.downloadProgress.isVisible = state.downloading
        binding.downloadProgress.isIndeterminate = state.indeterminate
        binding.downloadProgress.progress = state.progress
        binding.statusText.text = state.status
        binding.statusText.isVisible = state.status.isNotBlank()
        binding.cancelButton.isVisible = state.downloading
        binding.urlLayout.isEnabled = !state.downloading

        val error = state.error
        if (error == null) {
            lastError = null
        } else if (error != lastError) {
            lastError = error
            Snackbar.make(binding.root, error, Snackbar.LENGTH_LONG).show()
        }
    }

    private fun updateChoices(choices: List<FormatChoice>, selected: Int) {
        if (choices !== lastChoices) {
            lastChoices = choices
            binding.formatInput.setAdapter(FormatAdapter(this, choices))
        }
        val choice = choices.getOrNull(selected) ?: return
        if (binding.formatInput.text?.toString() != choice.displayTitle()) {
            binding.formatInput.setText(choice.displayTitle(), false)
        }
    }

    private fun playlistPreview(listing: PlaylistListing): String {
        val shown = listing.entries.take(5).mapIndexed { index, entry ->
            "${index + 1}. ${entry.title}"
        }
        return buildString {
            append(shown.joinToString("\n"))
            val rest = listing.entries.size - shown.size
            if (rest > 0) append("\n…and $rest more")
        }
    }

    private fun showPreview(source: PreviewSource?) {
        releasePlayer()
        if (source == null) {
            binding.playerView.isVisible = false
            binding.previewUnavailable.isVisible = true
            return
        }

        binding.previewUnavailable.isVisible = false
        binding.playerView.isVisible = true

        val headers = source.headers.toMutableMap()
        val userAgent = headers.entries
            .firstOrNull { it.key.equals("User-Agent", ignoreCase = true) }
            ?.value
            ?: "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        headers.keys.removeAll { it.equals("User-Agent", ignoreCase = true) }

        val dataSourceFactory = DefaultHttpDataSource.Factory()
            .setUserAgent(userAgent)
            .setDefaultRequestProperties(headers)
            .setAllowCrossProtocolRedirects(true)

        val exoPlayer = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory))
            .build()
        binding.playerView.player = exoPlayer
        exoPlayer.addListener(object : Player.Listener {
            override fun onPlayerError(error: PlaybackException) {
                binding.playerView.isVisible = false
                binding.previewUnavailable.isVisible = true
            }
        })
        exoPlayer.setMediaItem(MediaItem.fromUri(source.url))
        exoPlayer.prepare()
        player = exoPlayer
    }

    private fun releasePlayer() {
        if (::binding.isInitialized) {
            binding.playerView.player = null
        }
        player?.release()
        player = null
    }

    private fun hideKeyboard() {
        val manager = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
        manager.hideSoftInputFromWindow(binding.urlInput.windowToken, 0)
        binding.urlInput.clearFocus()
    }

    override fun onStop() {
        player?.pause()
        super.onStop()
    }

    override fun onDestroy() {
        releasePlayer()
        super.onDestroy()
    }

    /** Two-line dropdown: bold "1080p · H.264 · MP4", muted details below. */
    private class FormatAdapter(context: android.content.Context, items: List<FormatChoice>) :
        ArrayAdapter<FormatChoice>(context, R.layout.item_format, items) {

        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View =
            row(position, convertView, parent)

        override fun getDropDownView(position: Int, convertView: View?, parent: ViewGroup): View =
            row(position, convertView, parent)

        private fun row(position: Int, convertView: View?, parent: ViewGroup): View {
            val view = convertView ?: LayoutInflater.from(parent.context)
                .inflate(R.layout.item_format, parent, false)
            val choice = getItem(position) ?: return view
            view.findViewById<TextView>(R.id.formatTitle).text = choice.displayTitle()
            val detail = view.findViewById<TextView>(R.id.formatDetail)
            detail.text = choice.detail
            detail.isVisible = choice.detail.isNotBlank()
            return view
        }
    }
}

/** Title line, with a star for the suggested row. */
internal fun FormatChoice.displayTitle(): String = if (recommended) "★ $label" else label
