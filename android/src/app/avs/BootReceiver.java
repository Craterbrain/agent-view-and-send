package app.avs;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** After a restart, start listening again if the phone is paired. */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        if (!c.getSharedPreferences("avs", Context.MODE_PRIVATE).getString("token", "").isEmpty() && !Push.active(c)) {         // with push, the ntfy app wakes this app when something arrives
            try { c.startForegroundService(new Intent(c, ListenService.class)); } catch (Exception ignored) { }
        }
    }
}
