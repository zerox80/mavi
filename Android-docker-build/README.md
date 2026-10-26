# Android-Build mit Docker unter WSL

Unter Windows startet PowerShell den Build in **WSL 2**. Docker baut dort
Kotlin und den Rust-Core fuer ARMv7, ARM64, x86 und x86_64. Java, Android SDK,
NDK, Rust und cargo-ndk werden ausschliesslich im Container installiert.
GitHub Actions und Linux verwenden denselben Docker-Build.

Die Docker-Definition kommt aus
[zerox80/mavi-android-build](https://github.com/zerox80/mavi-android-build).
Dockerfile, Toolchain, Build-Einstieg und deren Tests werden dort gepflegt.
Dieses Verzeichnis enthaelt die Aufrufskripte und die Compose-Konfiguration
fuer den Anwendungs-Checkout. Der lokale Starter klont dessen `main` in ein
temporaeres WSL/Linux-Verzeichnis und baut daraus das Docker-Image.

## Voraussetzungen

- Windows: WSL 2 mit Ubuntu oder einer anderen Linux-Distribution.
- Docker Desktop mit aktivierter WSL-Integration fuer diese Distribution,
  alternativ Docker Engine inklusive Compose Plugin direkt in WSL.
- Eine laufende Linux Docker Engine und Internetzugriff fuer Abhaengigkeiten
  und die aktuellen Fork-Commits. Docker verwendet `linux/amd64`.
- Git innerhalb von WSL/Linux, um die aktuelle Docker-Definition zu laden.
- Ausreichend freier Docker-Speicher fuer SDK, NDK, Rust und Build-Caches.

In WSL muessen `docker info` und `docker compose version` funktionieren.
Android Studio, Java und Rust sind auf dem Windows-Host nicht erforderlich.
Beim Image-Build in `mavi-android-build` werden die Android-SDK-Lizenzen
automatisch akzeptiert.

## Windows / PowerShell

Aus dem Repository-Hauptverzeichnis:

```powershell
.\Android-docker-build\build.ps1
```

Standard ist `assembleDebug` in der WSL-Standarddistribution. Eine bestimmte
Distribution und andere Gradle-Aufgaben lassen sich explizit waehlen:

```powershell
.\Android-docker-build\build.ps1 -Distribution Ubuntu -GradleArguments @('assembleDebug', 'testDebugUnitTest')
```

## Direkt in WSL oder Linux

```bash
cd /mnt/c/Daten1/mavi-vpn
bash Android-docker-build/build.sh
bash Android-docker-build/build.sh testDebugUnitTest jacocoDebugUnitTestReport
```

Das Skript funktioniert auch in einem Linux-Checkout. Der Windows-Starter
uebersetzt den Checkout-Pfad automatisch mit `wslpath`.

## Ergebnisse

### GitHub Actions

Der Workflow **Build Android APK** (`.github/workflows/android.yml`) checkt bei
Pushes und Pull Requests `zerox80/mavi-android-build` auf `main` aus. Buildx
baut daraus das Docker-Image mit einem GitHub-Actions-Cache und laedt es in den
Runner. Der Build startet danach die APK-Kompilierung mit diesem Image.
Sobald der
Workflow auf dem Standardbranch liegt, ist er auch unter **Actions -> Build
Android APK -> Run workflow** manuell startbar. GitHub verwendet einen
Ubuntu-Runner mit Docker; WSL wird fuer den lokalen Windows-Starter verwendet.

Nach einem erfolgreichen Lauf gibt es zwei herunterladbare Artefakte:
`android-apk` mit der APK fuer alle vier Architekturen und
`android-build-lockfile` mit den beim Build verwendeten Fork-Revisionen.

### Lokale Dateien

- Debug-APK: `Android-docker-build/output/apk/debug/app-debug.apk`
- Release-APK: `Android-docker-build/output/apk/release/`
- App Bundle: `Android-docker-build/output/bundle/release/`
- Berichte: `Android-docker-build/output/reports/`
- JUnit-Ergebnisse: `Android-docker-build/output/test-results/`

Jeder Aufruf ersetzt die vorherigen Ergebnisse. Fehler liefern einen Exitcode
ungleich null; Testberichte werden auch bei fehlgeschlagenen Tests exportiert.
APKs und AABs werden vor dem Export auf alle vier nativen Bibliotheken geprueft.
Builds desselben Checkouts bitte nacheinander ausfuehren.

Die Quellen werden in das Linux-Dateisystem des Containers kopiert. Lokale
`local.properties`, JNI-Bibliotheken und Android-Studio-Buildreste werden dabei
ausgelassen. Der Checkout wird nur zum Lesen eingebunden; separate Mounts
erlauben das Schreiben von `Cargo.lock` und `output/`.

Vor jedem Build werden h2 (`master`), h3 und Quinn (jeweils `main`) aus den
`zerox80`-Forks aktualisiert und mit `check_fork_sources.py --check-remote`
geprueft. Ein geaendertes `Cargo.lock` wird ins Repository zurueckgeschrieben
und muss zusammen mit den zugehoerigen Aenderungen committed werden.

## Release und App Bundle

```powershell
.\Android-docker-build\build.ps1 -GradleArguments @('assembleRelease', '-Pmavi.oauthRedirectUri=https://vpn.example.com/oauth/callback')
.\Android-docker-build\build.ps1 -GradleArguments @('bundleRelease', '-Pmavi.oauthRedirectUri=https://vpn.example.com/oauth/callback')
```

Die eigene verifizierte HTTPS-App-Link-Adresse muss in Keycloak und
`assetlinks.json` hinterlegt sein. Die vorhandene Release-Konfiguration hat
keinen Signierschluessel; Release-Artefakte sind daher unsigniert.

## Caches und Toolchain

Docker-Volumes speichern Gradle-Downloads, Cargo-Downloads, Rust-Builds und den
Debug-Signierschluessel. Dadurch bleibt die Debug-Signatur zwischen lokalen
Builds erhalten. `docker compose down` erhaelt die Volumes. Das Loeschen der
Volumes entfernt auch den Debug-Schluessel; danach kann eine Neuinstallation
der App notwendig sein.

Das externe Image verwendet JDK 21, Android-Plattform 37.1, Build Tools 36.0.0
und 37.0.0, NDK 28.1.13356709 und Rust stable. SDK/NDK-Versionen im Repository
`mavi-android-build` muessen bei Aenderungen an `android/app/build.gradle.kts`
mitgezogen werden. Dessen Workflow **Validate Android build image** prueft die
Docker-Definition und den Build-Einstieg. Der Image- und APK-Build laeuft im
`mavi`-Workflow **Build Android APK**.

Ein vorhandener lokaler Checkout der Docker-Definition kann in WSL verwendet
werden, um das erneute Klonen zu ueberspringen:

```bash
export MAVI_ANDROID_BUILD_CONTEXT=/pfad/zu/mavi-android-build
bash Android-docker-build/build.sh assembleDebug
```
