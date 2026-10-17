package ru.klio.ritm;
import android.content.*;
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent) {Reminders.refresh(context);SyncWorker.schedule(context);SyncWorker.now(context);}
}
