# Samsung Call Recording Transcript Probe (com.seo.probe)

삼성 통화녹음의 "전사(Transcript)" 텍스트를 외부 앱이 읽을 수 있는 경로가 있는지
조사하는 단일 화면 안드로이드 탐침 앱.

- Kotlin + Jetpack Compose, minSdk 29 / targetSdk 34, package `com.seo.probe`
- 버튼 "탐침 실행" → 아래 7단계를 순서대로 수행, 결과를 스크롤+선택 가능한 텍스트로 표시
- "복사" / "공유" 버튼 제공, 모든 단계는 try/catch 로 감싸 앱이 죽지 않음

## 탐침 단계
1. 권한 요청: READ_MEDIA_AUDIO(13+) / READ_EXTERNAL_STORAGE(이하)
2. MediaStore.Audio.Media 에서 `Recordings/Call/` 최신 3개의 전체 컬럼명+값 (BLOB은 길이만)
3. 최신 1개 content URI 의 `query(uri, null, ...)` 전체 컬럼 (transcript/stt/text/lyric/description/comment 강조)
4. MediaMetadataRetriever 의 모든 METADATA_KEY_* 값
5. 파일 끝 64KB UTF-8 디코딩 → 한글/영문 문자열 추출
6. `com.sec.android.app.voicenote` 등 ContentProvider(authority/exported/readPermission) 열거,
   exported=true 는 루트 및 /recording /voice /transcript 등 query 시도(성공/실패+예외 기록)
7. AndroidManifest `<queries>` 로 위 패키지 가시성 허용

## 빌드
```
./gradlew assembleDebug
# 산출물: app/build/outputs/apk/debug/app-debug.apk
```
로컬에 Android SDK(platform 34, build-tools)가 설치돼 있어야 합니다.
SDK 없이 받으려면 GitHub Actions(`.github/workflows/probe-apk.yml`)가 push 시
자동 빌드하여 `probe-debug-apk` 아티팩트로 APK를 올립니다.
