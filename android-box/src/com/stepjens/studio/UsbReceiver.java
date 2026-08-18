package com.stepjens.studio;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.widget.Toast;

public class UsbReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (action.equals(UsbManager.ACTION_USB_DEVICE_ATTACHED)) {
            UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            if (device != null) {
                UsbManager usb = (UsbManager) context.getSystemService(Context.USB_SERVICE);
                PendingIntent pi = PendingIntent.getBroadcast(
                        context, 0,
                        new Intent(context, UsbReceiver.class),
                        PendingIntent.FLAG_IMMUTABLE);
                // Request permission with the base permission intent; the app will
                // re-open the studio after the user grants it.
                if (usb != null && !usb.hasPermission(device)) {
                    usb.requestPermission(device, pi);
                    Toast.makeText(context, "USB MIDI device attached", Toast.LENGTH_SHORT).show();
                }
            }
        }
    }
}