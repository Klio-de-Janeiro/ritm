package ru.klio.ritm;

import org.json.*;
import org.junit.Test;
import java.time.*;
import static org.junit.Assert.*;

public class DeadlineTest {
    @Test public void graceSlotsAndDismissalEligibilityFollowTheFinalDay() throws Exception {
        JSONObject task=new JSONObject().put("recurrence","once").put("start_date","2026-10-16").put("reminder_end_date","2026-10-16");
        JSONObject row=new JSONObject().put("date","2026-10-16").put("status","pending").put("dismissed_at",JSONObject.NULL);
        assertNull(Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T00:59:59")));
        assertEquals(LocalDateTime.parse("2026-10-17T01:00"),Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T01:30")));
        assertEquals(LocalDateTime.parse("2026-10-17T02:00"),Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T02:00:03")));
        assertNull(Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T03:00")));
        Deadline.decorate(task,row,LocalDateTime.parse("2026-10-17T01:59"));
        assertTrue(row.getBoolean("in_grace"));assertFalse(row.getBoolean("can_dismiss"));
        Deadline.decorate(task,row,LocalDateTime.parse("2026-10-17T02:00"));
        assertFalse(row.getBoolean("in_grace"));assertTrue(row.getBoolean("can_dismiss"));
        row.put("dismissed_at","2026-10-17T02:00:00+03:00");
        assertNull(Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T02:01")));
        task.put("reminder_end_date","2026-10-20");
        assertEquals(LocalDateTime.parse("2026-10-21T00:00"),Deadline.at(task,row));
    }
    @Test public void offlineDismissalReplaysAndCanBeRestored() throws Exception {
        JSONObject data=Model.empty();
        String date=LocalDate.now().minusDays(3).toString();
        JSONObject task=Model.clean(new JSONObject().put("title","Лекция"),false)
            .put("start_date",date).put("reminder_end_date",date)
            .put("id","task").put("archived_date",JSONObject.NULL);
        data.getJSONArray("tasks").put(task);
        Model.mutate(data,new JSONObject().put("kind","dismiss").put("task_id","task").put("date",date).put("dismissed",true));
        JSONObject view=Model.view(data,date),row=view.getJSONArray("occurrences").getJSONObject(0);
        assertFalse(row.isNull("dismissed_at"));assertEquals("pending",row.getString("status"));
        Model.mutate(data,new JSONObject().put("kind","dismiss").put("task_id","task").put("date",date).put("dismissed",false));
        assertTrue(Model.view(data,date).getJSONArray("occurrences").getJSONObject(0).isNull("dismissed_at"));
    }
}
