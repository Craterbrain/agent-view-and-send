package app.avs;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.widget.Toast;

/** Result of a package-installer session started by MainActivity.installApk. The system asks the person to confirm; this relays that. */
public class InstallResult extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        int status = i.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent confirm = i.getParcelableExtra(Intent.EXTRA_INTENT);
            if (confirm != null) c.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } else if (status == PackageInstaller.STATUS_SUCCESS) {
            Toast.makeText(c, "Installed", Toast.LENGTH_LONG).show();
        } else {
            String why = i.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
            Toast.makeText(c, "Install failed" + (why == null ? "" : ": " + why), Toast.LENGTH_LONG).show();
        }
    }
}
