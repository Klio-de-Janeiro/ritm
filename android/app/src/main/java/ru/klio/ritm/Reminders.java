package ru.klio.ritm;

import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.Build;
import org.json.*;
import java.time.*;
import java.util.*;

/** Локальные напоминания без интернета. Android может задерживать неточные будильники. */
public final class Reminders {
    private Reminders() {}
    static final String CHANNEL="ritm-reminders";
    public static boolean permitted(Context context) {
        NotificationManager manager=context.getSystemService(NotificationManager.class);
        return manager.areNotificationsEnabled() && (Build.VERSION.SDK_INT<33 || context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED);
    }
    @android.annotation.SuppressLint("MissingPermission") // Проверка POST_NOTIFICATIONS в permitted() перед notify().
    public static void refresh(Context context) {
        synchronized(Storage.LOCK) {
            Storage storage=new Storage(context);
            try {
                JSONObject data=storage.read(), settings=data.getJSONObject("settings");
                Intent alarmIntent=new Intent(context,ReminderReceiver.class);
                PendingIntent alarm=PendingIntent.getBroadcast(context,1,alarmIntent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
                AlarmManager alarms=context.getSystemService(AlarmManager.class);
                if(!settings.optBoolean("notifications",true)) {alarms.cancel(alarm);return;}
                NotificationManager manager=context.getSystemService(NotificationManager.class);
                manager.createNotificationChannel(new NotificationChannel(CHANNEL,"Напоминания Ритма",NotificationManager.IMPORTANCE_DEFAULT));
                LocalDate today=LocalDate.now(); ZonedDateTime now=ZonedDateTime.now();
                long next=today.plusDays(1).atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli();
                JSONObject view=Model.view(data,today.toString()),delivered=data.getJSONObject("delivered");
                Map<String,JSONObject> tasks=Model.map(view.getJSONArray("tasks"),false);
                JSONArray rows=view.getJSONArray("occurrences");boolean changed=false;
                for(int i=0;i<rows.length();i++) {
                    JSONObject row=rows.getJSONObject(i),task=tasks.get(row.getString("task_id"));
                    if(task==null || !task.isNull("archived_date") || !row.getString("status").equals("pending") || !row.optBoolean("reminder") || row.isNull("time")) continue;
                    if(!Model.remindersActive(task,today)) continue;
                    String day=row.getString("date");
                    if(task.getString("recurrence").equals("once") && !day.equals(task.getString("start_date"))) continue;
                    if(!day.equals(today.toString()) && !task.getString("recurrence").equals("once")) continue;
                    long when=LocalDateTime.parse(day+"T"+row.getString("time")).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
                    String key=Model.key(row)+"|"+row.getString("time");
                    String[] times=Model.dailyTimes(task,today);
                    if(times.length>0) {
                        LocalDateTime slot=null;
                        for(String value:times) {
                            LocalDateTime candidate=LocalDateTime.parse(today.toString()+"T"+value);
                            long at=candidate.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
                            if(candidate.isAfter(now.toLocalDateTime())) next=Math.min(next,at);
                            else slot=candidate;
                        }
                        if(slot==null) continue;
                        when=slot.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
                        key=Model.key(row)+"|"+slot.toString();
                        // Предыдущие пропущенные слоты не догоняются после сна.
                        if(delivered.has(key)) continue;
                    }
                    if(Model.hourInterval(task.getString("recurrence"))>0) {
                        LocalDateTime slot=Model.hourlySlot(task,now.toLocalDateTime());
                        long upcoming=Model.nextHourlySlot(task,now.toLocalDateTime()).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
                        next=Math.min(next,upcoming);
                        if(slot==null) continue;
                        when=slot.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
                        key=Model.key(row)+"|"+slot.toString();
                    }
                    if(delivered.has(key)) continue;
                    if(when>now.toInstant().toEpochMilli()) {next=Math.min(next,when);continue;}
                    if(!permitted(context)) continue;
                    Intent launch=new Intent(context,MainActivity.class);
                    PendingIntent open=PendingIntent.getActivity(context,0,launch,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
                    Notification notification=new Notification.Builder(context,CHANNEL).setSmallIcon(R.drawable.notification)
                        .setContentTitle(row.getString("title")).setContentText("Открой Ритм и отметь свой следующий шаг.")
                        .setContentIntent(open).setAutoCancel(true).build();
                    manager.notify(Model.key(row).hashCode(),notification);
                    delivered.put(key,today.toString());changed=true;
                }
                if(changed) storage.write(data);
                alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,Math.max(System.currentTimeMillis()+60_000,next),alarm);
            } catch(Exception error) {storage.error("Не удалось настроить напоминания: "+error.getMessage());}
        }
    }
}
