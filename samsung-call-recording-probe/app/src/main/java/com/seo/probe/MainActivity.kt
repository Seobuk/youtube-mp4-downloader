package com.seo.probe

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ProviderInfo
import android.database.Cursor
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.FileInputStream

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { ProbeScreen() }
    }
}

@Composable
fun ProbeScreen() {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()

    var result by remember { mutableStateOf("‘탐침 실행’ 버튼을 누르면 삼성 통화녹음 전사 접근 경로를 조사합니다.") }
    var running by remember { mutableStateOf(false) }

    val perm = if (Build.VERSION.SDK_INT >= 33)
        Manifest.permission.READ_MEDIA_AUDIO
    else
        Manifest.permission.READ_EXTERNAL_STORAGE

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        scope.launch {
            running = true
            result = "탐침 실행 중... (권한: ${if (granted) "허용" else "거부"})"
            val r = withContext(Dispatchers.IO) { ProbeRunner.run(context, granted) }
            result = r
            running = false
        }
    }

    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Column(modifier = Modifier.fillMaxSize().padding(12.dp)) {
                Text(
                    text = "삼성 통화녹음 전사(Transcript) 접근 탐침",
                    style = MaterialTheme.typography.titleMedium
                )
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(enabled = !running, onClick = { launcher.launch(perm) }) {
                        Text("탐침 실행")
                    }
                    OutlinedButton(enabled = !running, onClick = {
                        clipboard.setText(AnnotatedString(result))
                    }) { Text("복사") }
                    OutlinedButton(enabled = !running, onClick = {
                        try {
                            val send = Intent(Intent.ACTION_SEND).apply {
                                type = "text/plain"
                                putExtra(Intent.EXTRA_TEXT, result)
                            }
                            context.startActivity(Intent.createChooser(send, "탐침 결과 공유"))
                        } catch (e: Exception) {
                            // 공유 대상이 없어도 앱이 죽지 않게.
                        }
                    }) { Text("공유") }
                }
                Spacer(Modifier.height(8.dp))
                SelectionContainer(modifier = Modifier.weight(1f)) {
                    Text(
                        text = result,
                        modifier = Modifier.verticalScroll(rememberScrollState()),
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        lineHeight = 16.sp
                    )
                }
            }
        }
    }
}

/**
 * 모든 단계는 try/catch로 감싸 예외 메시지를 결과에 그대로 남기고,
 * 어떤 경우에도 앱이 죽지 않도록 한다.
 */
object ProbeRunner {

    private val HIGHLIGHT = listOf(
        "transcript", "stt", "text", "lyric", "description", "comment"
    )

    private val TARGET_PACKAGES = listOf(
        "com.sec.android.app.voicenote",
        "com.samsung.android.app.telephonyui",
        "com.samsung.android.incallui",
        "com.samsung.android.dialer",
        "com.android.phone"
    )

    fun run(context: Context, granted: Boolean): String {
        val sb = StringBuilder()
        sb.appendLine("================ 삼성 통화녹음 전사 탐침 결과 ================")
        sb.appendLine("시간: ${java.util.Date()}")
        sb.appendLine("기기: ${Build.MANUFACTURER} ${Build.MODEL} / Android ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
        sb.appendLine("요청 권한: ${if (Build.VERSION.SDK_INT >= 33) "READ_MEDIA_AUDIO" else "READ_EXTERNAL_STORAGE"} = ${if (granted) "허용됨" else "거부됨"}")
        sb.appendLine()

        val latestUri = try {
            section1and2(context, sb)
        } catch (e: Throwable) {
            sb.appendLine("[2] MediaStore 조회 중 예외: ${e.javaClass.name}: ${e.message}")
            null
        }

        runCatching { section3(context, sb, latestUri) }
            .onFailure { sb.appendLine("[3] 예외: ${it.javaClass.name}: ${it.message}") }

        runCatching { section4(context, sb, latestUri) }
            .onFailure { sb.appendLine("[4] 예외: ${it.javaClass.name}: ${it.message}") }

        runCatching { section5(context, sb, latestUri) }
            .onFailure { sb.appendLine("[5] 예외: ${it.javaClass.name}: ${it.message}") }

        runCatching { section6(context, sb) }
            .onFailure { sb.appendLine("[6] 예외: ${it.javaClass.name}: ${it.message}") }

        sb.appendLine()
        sb.appendLine("================ 탐침 종료 ================")
        return sb.toString()
    }

    // ---- [2] Recordings/Call/ 최신 3개 파일의 모든 컬럼 값 ----
    // 반환: 최신 1개 파일의 content URI (없으면 null) -> [3][4][5]에서 재사용
    private fun section1and2(context: Context, sb: StringBuilder): Uri? {
        sb.appendLine("---------- [2] MediaStore.Audio.Media : Recordings/Call/ 최신 3개 ----------")

        val collection = MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
        val sort = "${MediaStore.Audio.Media.DATE_ADDED} DESC"

        // 1차: RELATIVE_PATH LIKE 'Recordings/Call/%'
        var rows = queryRows(context, collection, "${MediaStore.Audio.Media.RELATIVE_PATH} LIKE ?", arrayOf("%Recordings/Call/%"), sort, 3)
        if (rows.isEmpty()) {
            sb.appendLine("※ RELATIVE_PATH LIKE 'Recordings/Call/%' 결과 없음 → '%Call%' 로 재시도")
            rows = queryRows(context, collection, "${MediaStore.Audio.Media.RELATIVE_PATH} LIKE ?", arrayOf("%Call%"), sort, 3)
        }
        if (rows.isEmpty()) {
            sb.appendLine("※ 통화녹음 폴더에서 파일을 찾지 못함. (권한 거부 / 통화녹음 미사용 / 경로 상이 가능)")
            sb.appendLine()
            return null
        }

        var latestUri: Uri? = null
        rows.forEachIndexed { idx, row ->
            sb.appendLine("■ 파일 #${idx + 1}")
            sb.appendLine("  columnNames(${row.columns.size}): ${row.columns.joinToString(", ")}")
            row.columns.forEachIndexed { i, name ->
                sb.appendLine("    - $name = ${row.values[i]}")
            }
            if (latestUri == null && row.id != null) {
                latestUri = ContentUris.withAppendedId(collection, row.id)
            }
            sb.appendLine()
        }
        sb.appendLine("최신 파일 content URI: $latestUri")
        sb.appendLine()
        return latestUri
    }

    private data class MediaRow(val columns: List<String>, val values: List<String>, val id: Long?)

    private fun queryRows(
        context: Context,
        uri: Uri,
        selection: String?,
        args: Array<String>?,
        sort: String?,
        limit: Int
    ): List<MediaRow> {
        val out = ArrayList<MediaRow>()
        // projection=null -> 모든 컬럼
        context.contentResolver.query(uri, null, selection, args, sort)?.use { c ->
            val cols = c.columnNames.toList()
            val idIdx = c.getColumnIndex(MediaStore.Audio.Media._ID)
            var count = 0
            while (c.moveToNext() && count < limit) {
                val values = ArrayList<String>(cols.size)
                for (i in cols.indices) {
                    values.add(cellToString(c, i))
                }
                val id = if (idIdx >= 0) runCatching { c.getLong(idIdx) }.getOrNull() else null
                out.add(MediaRow(cols, values, id))
                count++
            }
        }
        return out
    }

    private fun cellToString(c: Cursor, i: Int): String {
        return try {
            when (c.getType(i)) {
                Cursor.FIELD_TYPE_NULL -> "null"
                Cursor.FIELD_TYPE_INTEGER -> c.getLong(i).toString()
                Cursor.FIELD_TYPE_FLOAT -> c.getDouble(i).toString()
                Cursor.FIELD_TYPE_STRING -> c.getString(i) ?: "null"
                Cursor.FIELD_TYPE_BLOB -> {
                    val b = c.getBlob(i)
                    "<BLOB length=${b?.size ?: 0}>"
                }
                else -> c.getString(i) ?: "null"
            }
        } catch (e: Exception) {
            "<읽기 실패: ${e.javaClass.simpleName}: ${e.message}>"
        }
    }

    // ---- [3] 최신 파일 URI 를 projection=null 로 조회한 전체 컬럼 ----
    private fun section3(context: Context, sb: StringBuilder, latestUri: Uri?) {
        sb.appendLine("---------- [3] 최신 파일 content URI 전체 컬럼 (transcript/stt/text/lyric/description/comment 강조) ----------")
        if (latestUri == null) {
            sb.appendLine("※ 최신 파일 URI 없음 → 건너뜀")
            sb.appendLine()
            return
        }
        context.contentResolver.query(latestUri, null, null, null, null)?.use { c ->
            val cols = c.columnNames.toList()
            sb.appendLine("조회 가능한 컬럼 수: ${cols.size}")
            if (c.moveToFirst()) {
                cols.forEachIndexed { i, name ->
                    val hit = HIGHLIGHT.any { name.lowercase().contains(it) }
                    val mark = if (hit) ">>>>> " else "      "
                    val tag = if (hit) "  <== 주목! (전사 가능성)" else ""
                    sb.appendLine("$mark$name = ${cellToString(c, i)}$tag")
                }
            } else {
                sb.appendLine("※ 커서에 행이 없음. 컬럼명만 나열:")
                cols.forEach { name ->
                    val hit = HIGHLIGHT.any { name.lowercase().contains(it) }
                    sb.appendLine("${if (hit) ">>>>> " else "      "}$name")
                }
            }
            val matched = cols.filter { col -> HIGHLIGHT.any { col.lowercase().contains(it) } }
            sb.appendLine()
            sb.appendLine("★ 전사 관련 후보 컬럼: ${if (matched.isEmpty()) "없음" else matched.joinToString(", ")}")
        } ?: sb.appendLine("※ query 결과가 null")
        sb.appendLine()
    }

    // ---- [4] MediaMetadataRetriever 의 모든 METADATA_KEY_* ----
    private fun section4(context: Context, sb: StringBuilder, latestUri: Uri?) {
        sb.appendLine("---------- [4] MediaMetadataRetriever : 모든 METADATA_KEY_* ----------")
        if (latestUri == null) {
            sb.appendLine("※ 최신 파일 URI 없음 → 건너뜀")
            sb.appendLine()
            return
        }
        val mmr = MediaMetadataRetriever()
        try {
            mmr.setDataSource(context, latestUri)
            // 리플렉션으로 METADATA_KEY_* 상수 전부 열거
            val keyFields = MediaMetadataRetriever::class.java.fields
                .filter { it.name.startsWith("METADATA_KEY_") }
                .sortedBy { runCatching { it.getInt(null) }.getOrDefault(Int.MAX_VALUE) }
            var printed = 0
            for (f in keyFields) {
                val code = runCatching { f.getInt(null) }.getOrNull() ?: continue
                val v = try { mmr.extractMetadata(code) } catch (e: Exception) { null }
                if (v != null) {
                    sb.appendLine("  ${f.name}($code) = $v")
                    printed++
                }
            }
            if (printed == 0) sb.appendLine("  (값이 있는 메타데이터 키 없음)")
        } catch (e: Exception) {
            sb.appendLine("  setDataSource/추출 예외: ${e.javaClass.name}: ${e.message}")
        } finally {
            runCatching { mmr.release() }
        }
        sb.appendLine()
    }

    // ---- [5] 파일 끝 64KB -> UTF-8 디코딩 -> 한글/영문 문자열 추출 ----
    private fun section5(context: Context, sb: StringBuilder, latestUri: Uri?) {
        sb.appendLine("---------- [5] 파일 끝 64KB UTF-8 디코딩 → 한글/영문 문자열 ----------")
        if (latestUri == null) {
            sb.appendLine("※ 최신 파일 URI 없음 → 건너뜀")
            sb.appendLine()
            return
        }
        val tail = 64 * 1024
        context.contentResolver.openFileDescriptor(latestUri, "r")?.use { pfd ->
            val size = pfd.statSize
            FileInputStream(pfd.fileDescriptor).use { fis ->
                val channel = fis.channel
                val start = if (size > tail) size - tail else 0L
                channel.position(start)
                val len = (size - start).toInt().coerceAtLeast(0)
                val buf = ByteArray(len)
                var read = 0
                while (read < len) {
                    val r = fis.read(buf, read, len - read)
                    if (r < 0) break
                    read += r
                }
                sb.appendLine("파일 크기: $size bytes, 읽은 꼬리: $read bytes (offset $start~)")
                val text = String(buf, 0, read, Charsets.UTF_8)
                // 한글 음절/자모 + 영숫자/일부 기호가 4자 이상 이어지고 글자를 1개 이상 포함
                val regex = Regex("[\\uAC00-\\uD7A3\\u1100-\\u11FF\\u3130-\\u318Fa-zA-Z0-9 .,!?@:/()_\\-]{4,}")
                val letter = Regex("[\\uAC00-\\uD7A3\\u3130-\\u318Fa-zA-Z]")
                val hits = regex.findAll(text)
                    .map { it.value.trim() }
                    .filter { it.length >= 4 && letter.containsMatchIn(it) }
                    .distinct()
                    .take(200)
                    .toList()
                if (hits.isEmpty()) {
                    sb.appendLine("※ 사람이 읽을 만한 문자열 없음 (순수 오디오 바이트로 추정 — 꼬리에 전사 텍스트 미포함)")
                } else {
                    sb.appendLine("추출 문자열 ${hits.size}개:")
                    hits.forEach { sb.appendLine("  | $it") }
                }
            }
        } ?: sb.appendLine("※ openFileDescriptor 가 null")
        sb.appendLine()
    }

    // ---- [6] 삼성 보이스레코더/전화 패키지의 ContentProvider 탐색 ----
    private fun section6(context: Context, sb: StringBuilder) {
        sb.appendLine("---------- [6] 삼성 패키지 ContentProvider 탐색 (GET_PROVIDERS) ----------")
        val pm = context.packageManager
        val probePaths = listOf("", "/", "recording", "recordings", "voice", "transcript", "transcripts", "memo", "note")

        for (pkg in TARGET_PACKAGES) {
            sb.appendLine("■ 패키지: $pkg")
            val info = try {
                if (Build.VERSION.SDK_INT >= 33) {
                    pm.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(PackageManager.GET_PROVIDERS.toLong()))
                } else {
                    @Suppress("DEPRECATION")
                    pm.getPackageInfo(pkg, PackageManager.GET_PROVIDERS)
                }
            } catch (e: Exception) {
                sb.appendLine("  getPackageInfo 실패: ${e.javaClass.simpleName}: ${e.message}")
                sb.appendLine()
                continue
            }

            val providers: Array<ProviderInfo>? = info.providers
            if (providers.isNullOrEmpty()) {
                sb.appendLine("  노출된 provider 없음 (또는 가시성 제한)")
                sb.appendLine()
                continue
            }

            for (p in providers) {
                val authority = p.authority
                sb.appendLine("  · authority=$authority  exported=${p.exported}  readPermission=${p.readPermission}  grantUri=${p.grantUriPermissions}")
                if (p.exported && authority != null) {
                    for (auth in authority.split(";")) {
                        if (auth.isBlank()) continue
                        for (path in probePaths) {
                            val u = if (path.isEmpty()) Uri.parse("content://$auth")
                            else Uri.parse("content://$auth/$path")
                            try {
                                context.contentResolver.query(u, null, null, null, null).use { c ->
                                    if (c == null) {
                                        sb.appendLine("      NULL  $u  (query 반환 null)")
                                    } else {
                                        sb.appendLine("      OK    $u  rows=${c.count} cols=${c.columnCount} [${c.columnNames.joinToString(",")}]")
                                    }
                                }
                            } catch (e: Exception) {
                                sb.appendLine("      FAIL  $u  -> ${e.javaClass.simpleName}: ${e.message}")
                            }
                        }
                    }
                }
            }
            sb.appendLine()
        }
    }
}
