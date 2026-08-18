#!/bin/bash
# Start the Android-x86 11 VM and attach the webcam safely.
# Attaching the webcam too early (mid-boot) crashed the VM, so this
# waits for boot to fully complete first.
set -e

VM="Android x86 11"
# Webcam id: .1 = OBSBOT #1, .3 = OBSBOT #2 (see: VBoxManage list webcams)
CAM_ID="${CAM_ID:-.3}"
ADB="adb -s 127.0.0.1:5555"

echo "== 1. ensure VM is running =="
if ! vboxmanage list runningvms | grep -q "$VM"; then
    DISPLAY=:0 nohup VirtualBoxVM --startvm "$VM" >/tmp/opencode/vm.log 2>&1 &
    echo "   started VM (pid $!)"
fi

echo "== 2. wait for adb =="
for i in $(seq 1 60); do
    if adb devices 2>/dev/null | grep -q "127.0.0.1:5555"; then
        echo "   adb up after ~$((i*5))s"
        break
    fi
    sleep 5
done
adb connect 127.0.0.1:5555 >/dev/null 2>&1 || true
sleep 2

echo "== 3. wait for full boot =="
for i in $(seq 1 60); do
    B=$($ADB shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
    if [ "$B" = "1" ]; then
        echo "   boot_completed after ~$((i*5))s"
        break
    fi
    sleep 5
done

echo "== 4. attach webcams (after boot) =="
vboxmanage controlvm "$VM" webcam detach .1 >/dev/null 2>&1 || true
vboxmanage controlvm "$VM" webcam detach .3 >/dev/null 2>&1 || true
sleep 1
vboxmanage controlvm "$VM" webcam attach .1
vboxmanage controlvm "$VM" webcam attach .3
vboxmanage controlvm "$VM" webcam list
sleep 2

echo "== 5. restart camera servers so the UVC devices are enumerated =="
$ADB shell "su -c 'setprop ctl.restart cameraserver; setprop ctl.restart vendor.camera-provider-2-4'" 2>/dev/null
sleep 4
NC=$($ADB shell "su -c 'dumpsys media.camera 2>/dev/null'" 2>/dev/null | grep -oE "Number of camera devices: [0-9]+" | grep -oE "[0-9]+$")
echo "   cameras detected: ${NC:-0}"

echo "== 6. (re)launch Stepjens Studio so it re-opens the cameras =="
$ADB shell "am force-stop com.stepjens.studio" 2>/dev/null || true
sleep 1
$ADB shell "am start -n com.stepjens.studio/.MainActivity" 2>/dev/null || true
echo "== DONE =="
