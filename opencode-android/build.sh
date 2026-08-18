#!/bin/bash
# Build the OpenCode Android app (WebView client for the opencode web server).
# Works on desktop Linux and on-device with Termux.
set -e

APP="com.opencode.android"
SRC="src"
OUT="build"
TOOLS="tools"
ANDROID_JAR="${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-30/android.jar}"
if [ ! -f "$ANDROID_JAR" ]; then
    ANDROID_JAR="/data/data/com.termux/files/usr/share/aapt/android.jar"
fi

echo "== 1. clean =="
rm -rf "$OUT"; mkdir -p "$OUT/classes"
cp -r assets "$OUT/assets"
cp -r res "$OUT/res"

echo "== 2. compile Java =="
find "$SRC" -name "*.java" > "$OUT/sources.txt"
javac -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -d "$OUT/classes" @"$OUT/sources.txt"

echo "== 3. dex =="
DEX_DONE=0
if command -v d8 >/dev/null 2>&1; then
    d8 --lib "$ANDROID_JAR" --output "$OUT" $(find "$OUT/classes" -name "*.class")
    DEX_DONE=1
elif [ -f "$TOOLS/r8.jar" ] && command -v java >/dev/null 2>&1; then
    java -cp "$TOOLS/r8.jar" com.android.tools.r8.D8 --lib "$ANDROID_JAR" --output "$OUT" $(find "$OUT/classes" -name "*.class")
    DEX_DONE=1
fi
if [ "$DEX_DONE" = "0" ]; then
    echo "ERROR: need d8 or tools/r8.jar for dexing"; exit 1
fi

echo "== 4. package resources =="
if command -v aapt2 >/dev/null 2>&1; then
    (cd "$OUT" && aapt2 compile --dir res -o res.zip 2>/dev/null)
    aapt2 link -o "$OUT/base.apk" -I "$ANDROID_JAR" --manifest AndroidManifest.xml -A "$OUT/assets" "$OUT/res.zip" 2>/dev/null || \
    aapt2 link -o "$OUT/base.apk" -I "$ANDROID_JAR" --manifest AndroidManifest.xml -A "$OUT/assets" "$OUT/res.zip"
else
    aapt package -f -F "$OUT/base.apk" -S res -A "$OUT/assets" -M AndroidManifest.xml -I "$ANDROID_JAR"
fi

echo "== 5. add dex =="
if [ -f "$OUT/classes.dex" ]; then
    zip -uj "$OUT/base.apk" "$OUT/classes.dex"
fi

echo "== 6. align =="
zipalign -f 4 "$OUT/base.apk" "$OUT/aligned.apk"

echo "== 7. sign =="
KS="$OUT/debug.jks"
if [ ! -f "$KS" ]; then
    keytool -genkeypair -v -keystore "$KS" -alias androiddebugkey \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=Debug,O=OpenCode,C=US" >/dev/null 2>&1
fi
apksigner sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --out "$OUT/OpenCode.apk" "$OUT/aligned.apk"

echo "== DONE =="
echo "Install:  adb install $OUT/OpenCode.apk"
