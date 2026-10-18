package ru.klio.ritm;

import org.json.JSONObject;
import java.time.*;

final class Deadline {
    static LocalDateTime at(JSONObject task,JSONObject row) throws Exception {
        String day=row.getString("date");
        if(task.getString("recurrence").equals("once") && day.equals(task.getString("start_date")) && !task.isNull("reminder_end_date")) day=task.getString("reminder_end_date");
        return LocalDate.parse(day).plusDays(1).atStartOfDay();
    }
    static LocalDateTime slot(JSONObject task,JSONObject row,LocalDateTime now) throws Exception {
        if(!row.isNull("dismissed_at")) return null;
        if(task.getString("recurrence").equals("once") && !row.getString("date").equals(task.getString("start_date"))) return null;
        if(!task.isNull("reminder_end_date") && row.getString("date").compareTo(task.getString("reminder_end_date"))>0) return null;
        LocalDateTime deadline=at(task,row),first=deadline.plusHours(1),last=deadline.plusHours(2);
        if(now.isBefore(first) || !now.isBefore(last.plusMinutes(5))) return null;
        return now.isBefore(last)?first:last;
    }
    static void decorate(JSONObject task,JSONObject row,LocalDateTime now) throws Exception {
        LocalDateTime deadline=at(task,row),until=deadline.plusHours(2);
        boolean pending=row.getString("status").equals("pending") && row.isNull("dismissed_at");
        row.put("deadline_at",deadline.atZone(ZoneId.systemDefault()).toOffsetDateTime().toString())
            .put("grace_until",until.atZone(ZoneId.systemDefault()).toOffsetDateTime().toString())
            .put("in_grace",pending && !now.isBefore(deadline) && now.isBefore(until))
            .put("can_dismiss",pending && !LocalDate.parse(row.getString("date")).isAfter(now.toLocalDate()))
            .put("in_unfinished",row.getString("status").equals("pending") && (!row.isNull("dismissed_at") || !now.isBefore(until)));
    }
}
