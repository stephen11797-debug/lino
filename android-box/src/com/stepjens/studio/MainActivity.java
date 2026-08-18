package com.stepjens.studio;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Base64;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;

public class MainActivity extends Activity {

    private WebView web;
    private static final int REQ_PERM = 1001;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        android.webkit.WebView.setWebContentsDebuggingEnabled(true);
        web = new WebView(this);
        web.setLayoutParams(new android.view.ViewGroup.LayoutParams(
            android.view.ViewGroup.LayoutParams.MATCH_PARENT,
            android.view.ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(false);
        s.setUseWideViewPort(false);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setUserAgentString(s.getUserAgentString() + " StepjensStudio/1.0");
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);

        web.addJavascriptInterface(new SaveBridge(), "SaveBridge");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(android.webkit.WebView view, String url) {
                super.onPageFinished(view, url);
                view.evaluateJavascript(
                    "console.log('DIMS: innerWidth='+window.innerWidth+" +
                    " ' docW='+document.documentElement.scrollWidth+" +
                    " ' bodyW='+document.body.scrollWidth+" +
                    " ' scale='+window.devicePixelRatio+" +
                    " ' contW='+(document.querySelector('.container')?document.querySelector('.container').offsetWidth:0));",
                    null);
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                // Grant camera + mic for the studio WebView
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        try {
                            request.grant(request.getResources());
                        } catch (Exception e) {
                            request.deny();
                        }
                    }
                });
            }
            @Override
            public boolean onJsAlert(WebView view, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                        .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                            @Override public void onClick(android.content.DialogInterface d, int w) { result.confirm(); }
                        })
                        .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                            @Override public void onCancel(android.content.DialogInterface d) { result.cancel(); }
                        }).show();
                return true;
            }
            @Override
            public boolean onJsConfirm(WebView view, String url, String message, final JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                        .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                            @Override public void onClick(android.content.DialogInterface d, int w) { result.confirm(); }
                        })
                        .setNegativeButton("Cancel", new android.content.DialogInterface.OnClickListener() {
                            @Override public void onClick(android.content.DialogInterface d, int w) { result.cancel(); }
                        })
                        .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                            @Override public void onCancel(android.content.DialogInterface d) { result.cancel(); }
                        }).show();
                return true;
            }
            @Override
            public boolean onJsPrompt(WebView view, String url, String message, String defaultValue, final JsPromptResult result) {
                final android.widget.EditText input = new android.widget.EditText(MainActivity.this);
                input.setText(defaultValue);
                new AlertDialog.Builder(MainActivity.this).setTitle(message).setView(input)
                        .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                            @Override public void onClick(android.content.DialogInterface d, int w) { result.confirm(input.getText().toString()); }
                        })
                        .setNegativeButton("Cancel", new android.content.DialogInterface.OnClickListener() {
                            @Override public void onClick(android.content.DialogInterface d, int w) { result.cancel(); }
                        })
                        .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                            @Override public void onCancel(android.content.DialogInterface d) { result.cancel(); }
                        }).show();
                return true;
            }
        });

        requestRuntimePermissions();

        // Try to get a direct connection to a USB device so the WebView can use it
        Handler h = new Handler(Looper.getMainLooper());
        h.postDelayed(new Runnable() {
            @Override public void run() {
                web.loadUrl("file:///android_asset/studio.html");
            }
        }, 300);
    }

    private void requestRuntimePermissions() {        if (Build.VERSION.SDK_INT >= 23) {
            String[] perms = new String[]{
                android.Manifest.permission.CAMERA,
                android.Manifest.permission.RECORD_AUDIO
            };
            requestPermissions(perms, REQ_PERM);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_PERM) {
            AudioManager am = (AudioManager) getSystemService(Context.AUDIO_SERVICE);
            if (am != null) am.setStreamVolume(AudioManager.STREAM_MUSIC, am.getStreamMaxVolume(AudioManager.STREAM_MUSIC), 0);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    class SaveBridge {
        @JavascriptInterface
        public void saveFile(String name, String dataUrl, String mime) {
            try {
                if (name == null || dataUrl == null) return;
                if (dataUrl.indexOf(',') >= 0) dataUrl = dataUrl.substring(dataUrl.indexOf(',') + 1);
                byte[] bytes = Base64.decode(dataUrl, Base64.DEFAULT);
                OutputStream out = null;
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.Downloads.DISPLAY_NAME, name);
                    cv.put(MediaStore.Downloads.MIME_TYPE, mime == null ? "application/octet-stream" : mime);
                    cv.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Stepjens");
                    Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
                    if (uri != null) {
                        out = getContentResolver().openOutputStream(uri);
                        out.write(bytes);
                        out.close();
                        Toast.makeText(MainActivity.this, "Saved: Downloads/Stepjens/" + name, Toast.LENGTH_LONG).show();
                        return;
                    }
                }
                File dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "Stepjens");
                if (!dir.exists()) dir.mkdirs();
                File f = new File(dir, name);
                out = new FileOutputStream(f);
                out.write(bytes);
                out.close();
                Toast.makeText(MainActivity.this, "Saved: " + f.getAbsolutePath(), Toast.LENGTH_LONG).show();
            } catch (Exception e) {
                Log.e("SaveBridge", "save failed", e);
            }
        }
    }
}
