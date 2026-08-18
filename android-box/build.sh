#!/data/data/com.termux/files/usr/bin/bash
# Build Stepjens Studio APK entirely on the device with Termux.
# Run once:  pkg install aapt build-tools apksigner zipalign openjdk-17
set -e

APP="com.stepjens.studio"
SRC="src"
OUT="build"
ANDROID_JAR="${ANDROID_JAR:-/data/data/com.termux/files/usr/share/aapt/android.jar}"

echo "== 1. clean =="
rm -rf "$OUT"; mkdir -p "$OUT/classes"
cp studio.html assets/studio.html 2>/dev/null || cp ../studio.html assets/studio.html

echo "== 2. compile Java =="
find "$SRC" -name "*.java" > "$OUT/sources.txt"
javac -source 8 -target 8 -bootclasspath "$ANDROID_JAR" -d "$OUT/classes" @"$OUT/sources.txt" 2>/dev/null || \
javac -source 8 -target 8 -classpath "$ANDROID_JAR" -d "$OUT/classes" @"$OUT/sources.txt"

echo "== 3. dex =="
if command -v d8 >/dev/null 2>&1; then
    d8 --lib "$ANDROID_JAR" --output "$OUT" $(find "$OUT/classes" -name "*.class")
else
    dx --dex --output="$OUT/classes.dex" "$OUT/classes"
fi

echo "== 4. package resources =="
if command -v aapt2 >/dev/null 2>&1; then
    aapt2 compile --dir res -o "$OUT/res.zip" 2>/dev/null
    aapt2 link -o "$OUT/base.apk" -I "$ANDROID_JAR" \
        --manifest AndroidManifest.xml -A assets $OUT/res.zip 2>/dev/null
else
    aapt package -f -m -J gen -S res -M AndroidManifest.xml -I "$ANDROID_JAR" 2>/dev/null
    aapt package -f -F "$OUT/base.apk" -S res -A assets -M AndroidManifest.xml -I "$ANDROID_JAR"
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
        -dname "CN=Debug,O=Stepjens,C=US"
fi
apksigner sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --out "$OUT/StepjensStudio.apk" "$OUT/aligned.apk"

echo "== DONE =="
echo "Install:  adb install $OUT/StepjensStudio.apk"