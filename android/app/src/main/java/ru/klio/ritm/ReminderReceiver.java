package ru.klio.ritm;
import android.content.*;
public class ReminderReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent) {Reminders.refresh(context);}
}
