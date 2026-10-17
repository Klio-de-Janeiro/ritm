# Personal Android release build. No administrator privileges required.
$ErrorActionPreference = 'Stop'
$RitmRoot = $PSScriptRoot
$RitmJavaDir = $null
$RitmSdk = $null
$RitmOldJava = $env:JAVA_HOME
$RitmOldAndroid = $env:ANDROID_HOME
$RitmOldLocation = Get-Location
try {
    $RitmCandidates = @($env:JAVA_HOME, "$env:ProgramFiles\Android\Android Studio\jbr", "$env:LOCALAPPDATA\Programs\Android Studio\jbr")
    foreach ($RitmCandidate in $RitmCandidates) {
        if ($RitmCandidate -and (Test-Path "$RitmCandidate\bin\java.exe") -and (Test-Path "$RitmCandidate\bin\keytool.exe") -and (Test-Path "$RitmCandidate\release")) {
            $RitmRelease = Get-Content "$RitmCandidate\release" -Raw
            if ($RitmRelease -match 'JAVA_VERSION="(17|18|19|20|21|22|23)([.\-"])') {
                $RitmJavaDir = $RitmCandidate
                break
            }
        }
    }
    if (-not $RitmJavaDir) { throw 'Install Android Studio (with its JBR), or set JAVA_HOME to JDK 17 or 21. See ANDROID_GPT_RU.md.' }
    $env:JAVA_HOME = $RitmJavaDir
    foreach ($RitmCandidate in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, "$env:LOCALAPPDATA\Android\Sdk")) {
        if ($RitmCandidate -and (Test-Path $RitmCandidate)) { $RitmSdk = $RitmCandidate; break }
    }
    if (-not $RitmSdk) { throw 'Android SDK not found. Start Android Studio and finish its setup wizard.' }
    $env:ANDROID_HOME = $RitmSdk
    if (-not (Test-Path "$RitmSdk\platforms\android-35\android.jar") -or -not (Test-Path "$RitmSdk\build-tools\35.0.0\apksigner.bat")) {
        $RitmSdkManager = Get-ChildItem "$RitmSdk\cmdline-tools\*\bin\sdkmanager.bat" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if (-not $RitmSdkManager) { throw 'Install Android SDK Command-line Tools (latest), Android SDK Platform 35 and Build-Tools 35.0.0 in Android Studio > SDK Manager.' }
        Write-Host 'Review Android SDK licenses below, then install the required SDK packages.'
        & $RitmSdkManager.FullName --licenses
        if ($LASTEXITCODE -ne 0) { throw 'SDK licenses were not accepted.' }
        & $RitmSdkManager.FullName 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
        if ($LASTEXITCODE -ne 0) { throw 'Android SDK installation failed.' }
    }
    if (-not (Test-Path "$RitmRoot\Ritm.png")) { throw 'Place Ritm.png beside README.md before building.' }
    $RitmTools = Join-Path $RitmRoot '.android-tools'
    New-Item -ItemType Directory -Path $RitmTools -Force | Out-Null
    $RitmGradle = Join-Path $RitmTools 'gradle-8.11.1\bin\gradle.bat'
    if (-not (Test-Path $RitmGradle)) {
        $RitmArchive = Join-Path $RitmTools 'gradle-8.11.1-bin.zip'
        $RitmHash = 'f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        if (-not (Test-Path $RitmArchive) -or (Get-FileHash $RitmArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $RitmHash) {
            Write-Host 'Downloading verified Gradle 8.11.1...'
            Invoke-WebRequest 'https://services.gradle.org/distributions/gradle-8.11.1-bin.zip' -OutFile $RitmArchive -UseBasicParsing
        }
        if ((Get-FileHash $RitmArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $RitmHash) { throw 'Gradle SHA-256 verification failed. No downloaded code was executed.' }
        Expand-Archive -Path $RitmArchive -DestinationPath $RitmTools -Force
    }
    $RitmSigning = Join-Path $RitmRoot '.android-signing'
    New-Item -ItemType Directory -Path $RitmSigning -Force | Out-Null
    $RitmKey = Join-Path $RitmSigning 'ritm.jks'
    if (-not (Test-Path $RitmKey)) {
        & "$RitmJavaDir\bin\keytool.exe" -genkeypair -keystore $RitmKey -storetype JKS -storepass ritm-personal -keypass ritm-personal -alias ritm -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=Ritm Personal' -noprompt
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the local signing key.' }
    }
    Set-Location (Join-Path $RitmRoot 'android')
    & $RitmGradle --no-daemon --console plain testDebugUnitTest lintRelease assembleRelease
    if ($LASTEXITCODE -ne 0) { throw 'Android tests or build failed. No new APK was confirmed.' }
    $RitmApk = Join-Path $RitmRoot 'android\app\build\outputs\apk\release\app-release.apk'
    if (-not (Test-Path $RitmApk)) { throw 'Gradle did not produce the expected APK.' }
    & "$RitmSdk\build-tools\35.0.0\apksigner.bat" verify --verbose $RitmApk
    if ($LASTEXITCODE -ne 0) { throw 'APK signature verification failed.' }
    New-Item -ItemType Directory -Path (Join-Path $RitmRoot 'dist') -Force | Out-Null
    Copy-Item $RitmApk (Join-Path $RitmRoot 'dist\Ritm-Android.apk') -Force
    Write-Host "BUILD OK: $RitmRoot\dist\Ritm-Android.apk" -ForegroundColor Green
    Write-Host 'Keep .android-signing\ritm.jks: it is needed to update the installed app without deleting its data.'
} catch {
    Write-Host "BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    Set-Location $RitmOldLocation
    $env:JAVA_HOME = $RitmOldJava
    $env:ANDROID_HOME = $RitmOldAndroid
}
