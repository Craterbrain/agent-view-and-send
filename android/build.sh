#!/bin/sh
# Builds avs.apk with the plain Android SDK tools (no Gradle). SDK: ~/Android/sdk. Output: android/build/avs.apk
set -e
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-$HOME/Android/sdk}; BT=$SDK/build-tools/35.0.0; JAR=$SDK/platforms/android-35/android.jar
rm -rf build && mkdir -p build/gen build/classes
$BT/aapt2 compile --dir res -o build/res.zip
$BT/aapt2 link -o build/base.apk -I $JAR --manifest AndroidManifest.xml --min-sdk-version 26 --target-sdk-version 34 --version-code 5 --version-name 1.4 --java build/gen build/res.zip
javac --release 17 -cp $JAR -d build/classes $(find src build/gen -name '*.java') 2>&1 | grep -v '^Note:' || true
$BT/d8 --release --lib $JAR --min-api 26 --output build $(find build/classes -name '*.class')
cp build/base.apk build/unsigned.apk && (cd build && zip -q unsigned.apk classes.dex)
[ -f debug.keystore ] || keytool -genkeypair -keystore debug.keystore -alias avs -keyalg RSA -keysize 2048 -validity 10000 -storepass android -keypass android -dname "CN=AVS" 2>/dev/null
$BT/zipalign -f 4 build/unsigned.apk build/aligned.apk
$BT/apksigner sign --ks debug.keystore --ks-pass pass:android --key-pass pass:android --out build/avs.apk build/aligned.apk
echo "built $(pwd)/build/avs.apk ($(du -h build/avs.apk | cut -f1))"
