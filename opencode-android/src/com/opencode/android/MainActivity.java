package com.opencode.android;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.HttpAuthHandler;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

public class MainActivity extends Activity {

    private WebView web;
    private SharedPreferences prefs;
    private static final String PREFS = "opencode_settings";
    private static final String KEY_URL = "server_url";
    private static final String KEY_USER = "server_user";
    private static final String KEY_PASS = "server_pass";
    private static final String DEFAULT_USER = "opencode";
    private static final String SETTINGS_PAGE = "file:///android_asset/settings.html";

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        web = new WebView(this);
        web.setLayoutParams(new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT));

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        s.setUserAgentString(s.getUserAgentString() + " OpenCode-Android/1.0");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler,
                    String host, String realm) {
                String user = prefs.getString(KEY_USER, DEFAULT_USER);
                String pass = prefs.getString(KEY_PASS, "");
                view.setHttpAuthUsernamePassword(host, realm, user, pass);
                handler.proceed(user, pass);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError error) {
                if (req.getUrl().toString().startsWith("http")
                        && error.getErrorCode() != WebViewClient.ERROR_HOST_LOOKUP) {
                    showServerError(error.getDescription().toString());
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
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
        });

        web.addJavascriptInterface(new AppBridge(), "Android");

        FrameLayout root = new FrameLayout(this);
        root.addView(web);

        final TextView gear = new TextView(this);
        gear.setText("\u2699");
        gear.setTextSize(22);
        gear.setTextColor(Color.WHITE);
        gear.setGravity(Gravity.CENTER);
        gear.setBackgroundColor(0x88000000);
        gear.setPadding(10, 10, 10, 10);
        FrameLayout.LayoutParams gp = new FrameLayout.LayoutParams(
            (int) (46 * getResources().getDisplayMetrics().density),
            (int) (46 * getResources().getDisplayMetrics().density));
        gp.gravity = Gravity.BOTTOM | Gravity.RIGHT;
        gp.setMargins(0, 0, 16, 16);
        root.addView(gear, gp);
        gear.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                web.loadUrl(SETTINGS_PAGE);
            }
        });

        setContentView(root);

        String url = prefs.getString(KEY_URL, "");
        if (!url.isEmpty()) {
            web.loadUrl(url);
        } else {
            web.loadUrl(SETTINGS_PAGE);
        }
    }

    private void showServerError(String msg) {
        runOnUiThread(new Runnable() {
            @Override public void run() {
                if (web.getUrl() == null) return;
                new AlertDialog.Builder(MainActivity.this)
                    .setTitle("Can't reach server")
                    .setMessage("Could not connect to " + web.getUrl() + "\n\n" + msg
                        + "\n\nMake sure the server is running and reachable, then tap the gear to change settings.")
                    .setPositiveButton("OK", null)
                    .show();
            }
        });
    }

    private class AppBridge {
        @JavascriptInterface
        public void save(String url, String user, String password) {
            final String u = url == null ? "" : url.trim();
            final String us = (user == null || user.isEmpty()) ? DEFAULT_USER : user.trim();
            final String p = password == null ? "" : password;
            runOnUiThread(new Runnable() {
                @Override public void run() {
                    prefs.edit().putString(KEY_URL, u)
                        .putString(KEY_USER, us)
                        .putString(KEY_PASS, p)
                        .apply();
                    if (!u.isEmpty()) {
                        web.loadUrl(u);
                    }
                }
            });
        }

        @JavascriptInterface
        public String getSettings() {
            String u = prefs.getString(KEY_URL, "");
            String us = prefs.getString(KEY_USER, DEFAULT_USER);
            String p = prefs.getString(KEY_PASS, "");
            return u + "\n" + us + "\n" + p;
        }

        @JavascriptInterface
        public String getLocalIpHint() {
            return "";
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
}
