package app.avs;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;

/** What the UnifiedPush distributor (the ntfy app) sends this app. */
public class PushReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        String a = i.getAction();
        if (a == null) return;
        SharedPreferences p = Push.prefs(c);
        String mine = p.getString("pushToken", "");
        if (mine.isEmpty() || !mine.equals(i.getStringExtra("token"))) return;           // not meant for this app's registration
        switch (a) {
            case "org.unifiedpush.android.connector.NEW_ENDPOINT": {
                String e = i.getStringExtra("endpoint");
                if (e != null && !e.isEmpty()) {
                    if (!e.equals(p.getString("endpoint", ""))) p.edit().putString("endpoint", e).putBoolean("endpointSent", false).apply();
                    Push.syncAsync(c);                                                    // the PC learns it; the ntfy stream stops once it has
                }
                Push.ack(c, i);
                break;
            }
            case "org.unifiedpush.android.connector.MESSAGE": {
                byte[] b = i.getByteArrayExtra("bytesMessage");
                String s = b != null ? new String(b, StandardCharsets.UTF_8) : i.getStringExtra("message");
                if (s != null) {
                    String title = "AVS", text = s;
                    try { JSONObject m = new JSONObject(s); title = m.optString("title", "AVS"); text = m.optString("text", m.optString("message", s)); } catch (Exception ignored) { }
                    Alerts.show(c, title, text);
                }
                Push.ack(c, i);
                break;
            }
            case "org.unifiedpush.android.connector.UNREGISTERED":
            case "org.unifiedpush.android.connector.REGISTRATION_FAILED":
                Push.forgetAsync(c);                                                      // back to the ntfy stream
                try { c.startForegroundService(new Intent(c, ListenService.class)); } catch (Exception ignored) { }
                break;
            default: break;                                                               // TEMP_UNAVAILABLE: nothing to do
        }
    }
}
