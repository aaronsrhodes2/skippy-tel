package live.makerportal.skippy.launcher;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * The bedroom media box's home screen (the Captain, 2026-11-12: "give it a Skippy makeover"). One full-screen WebView showing assets/index.html, which is the
 * whole interface; this class is only the bridge it needs: start an app, start Steam Big Picture through Moonlight, list the installed apps, and probe the
 * house's services for the status lamps. It is the HOME app, so it runs at boot: it starts Steam by itself (once per boot, any key cancels it).
 */
public class MainActivity extends Activity {
    // Moonlight's own shortcut entry (what a pinned Moonlight shortcut uses): the PC by its Sunshine uniqueid, the app by name.
    static final String PC_UUID = "1A9CFE30-4D90-E423-F689-1F01449CCA5D";
    static final String STEAM_APP = "Steam Big Picture";
    static boolean coldStart = true;                     // true until the page has asked once in this process: the boot, not a HOME press

    WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.setBackgroundColor(0xFF000000);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setVerticalScrollBarEnabled(false);
        web.addJavascriptInterface(new Bridge(), "Skippy");
        web.loadUrl("file:///android_asset/index.html");
        setContentView(web);
        web.requestFocus();
    }

    @Override
    protected void onNewIntent(Intent i) {              // HOME pressed again: back to the page's first screen
        super.onNewIntent(i);
        web.evaluateJavascript("window.home && window.home()", null);
    }

    @Override
    public boolean onKeyDown(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_BUTTON_A) {         // a controller's A is the page's Enter
            return web.dispatchKeyEvent(new KeyEvent(e.getAction(), KeyEvent.KEYCODE_ENTER));
        }
        if (code == KeyEvent.KEYCODE_BACK) return true;  // a launcher has nowhere to go back to
        return super.onKeyDown(code, e);
    }

    @Override
    public boolean onKeyUp(int code, KeyEvent e) {
        if (code == KeyEvent.KEYCODE_BUTTON_A) {
            return web.dispatchKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_ENTER));
        }
        return super.onKeyUp(code, e);
    }

    class Bridge {
        @JavascriptInterface
        public boolean cold() {                          // asked once per process: is this the boot?
            boolean c = coldStart;
            coldStart = false;
            return c;
        }

        @JavascriptInterface
        public boolean launch(String pkg) {
            PackageManager pm = getPackageManager();
            Intent i = pm.getLeanbackLaunchIntentForPackage(pkg);
            if (i == null) i = pm.getLaunchIntentForPackage(pkg);
            if (i == null) return false;
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
            return true;
        }

        @JavascriptInterface
        public boolean steam() {                         // Steam Big Picture on skippy-pc, straight away, through Moonlight
            try {
                Intent i = new Intent();
                i.setComponent(new ComponentName("com.limelight", "com.limelight.ShortcutTrampoline"));
                i.putExtra("UUID", PC_UUID);
                i.putExtra("Name", STEAM_APP);
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(i);
                return true;
            } catch (Exception e) {
                return launch("com.limelight");
            }
        }

        @JavascriptInterface
        public void settings() {
            Intent i = new Intent(android.provider.Settings.ACTION_SETTINGS);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        }

        @JavascriptInterface
        public String apps() {                           // [{"l":"label","p":"package"}, ...] of everything launchable but this launcher
            PackageManager pm = getPackageManager();
            List<String[]> out = new ArrayList<>();
            for (String cat : new String[]{Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER}) {
                Intent q = new Intent(Intent.ACTION_MAIN).addCategory(cat);
                for (ResolveInfo r : pm.queryIntentActivities(q, 0)) {
                    String p = r.activityInfo.packageName;
                    if (p.equals(getPackageName())) continue;
                    boolean seen = false;
                    for (String[] o : out) if (o[1].equals(p)) seen = true;
                    if (!seen) out.add(new String[]{String.valueOf(r.loadLabel(pm)), p});
                }
            }
            Collections.sort(out, (a, b) -> a[0].compareToIgnoreCase(b[0]));
            StringBuilder sb = new StringBuilder("[");
            for (int k = 0; k < out.size(); k++) {
                if (k > 0) sb.append(',');
                sb.append("{\"l\":\"").append(out.get(k)[0].replace("\\", "\\\\").replace("\"", "\\\"")).append("\",\"p\":\"").append(out.get(k)[1]).append("\"}");
            }
            return sb.append(']').toString();
        }

        @JavascriptInterface
        public void probe(final String id, final String host, final int port) {   // a status lamp: does host:port answer? -> window.lamp(id, true|false)
            new Thread(() -> {
                boolean ok = false;
                try (Socket s = new Socket()) {
                    s.connect(new InetSocketAddress(host, port), 1500);
                    ok = true;
                } catch (Exception ignored) {
                }
                final boolean up = ok;
                runOnUiThread(() -> web.evaluateJavascript("window.lamp && window.lamp('" + id + "'," + up + ")", null));
            }).start();
        }
    }
}
