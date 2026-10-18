package ru.klio.ritm;

import org.json.*;
import org.junit.Test;
import java.time.*;
import static org.junit.Assert.*;

public class DeadlineTest {
    @Test public void todaysTaskCanBeDismissedEarlyAndFutureTaskCannot() throws Exception {
        JSONObject data=Model.empty();String today=LocalDate.now().toString();
        JSONObject task=Model.clean(new JSONObject().put("title","Лекция"),false).put("id","today").put("archived_date",JSONObject.NULL);
        data.getJSONArray("tasks").put(task);
        Model.mutate(data,new JSONObject().put("kind","dismiss").put("task_id","today").put("date",today).put("dismissed",true));
        JSONObject row=Model.map(Model.view(data,today).getJSONArray("occurrences"),true).get("today|"+today);
        assertTrue(row.getBoolean("in_unfinished"));assertFalse(row.isNull("dismissed_at"));
        Model.mutate(data,new JSONObject().put("kind","dismiss").put("task_id","today").put("date",today).put("dismissed",false));
        row=Model.map(Model.view(data,today).getJSONArray("occurrences"),true).get("today|"+today);
        assertFalse(row.getBoolean("in_unfinished"));
        String tomorrow=LocalDate.now().plusDays(1).toString();
        task=Model.clean(new JSONObject().put("title","Завтра").put("start_date",tomorrow),false).put("id","future").put("archived_date",JSONObject.NULL);
        data.getJSONArray("tasks").put(task);
        try {
            Model.mutate(data,new JSONObject().put("kind","dismiss").put("task_id","future").put("date",tomorrow).put("dismissed",true));
            fail("Future task cannot be dismissed");
        } catch(IllegalArgumentException expected) { assertTrue(expected.getMessage().contains("Будущее")); }
    }
    @Test public void graceSlotsAndDismissalEligibilityFollowTheFinalDay() throws Exception {
        JSONObject task=new JSONObject().put("recurrence","once").put("start_date","2026-10-16").put("reminder_end_date","2026-10-16");
        JSONObject row=new JSONObject().put("date","2026-10-16").put("status","pending").put("dismissed_at",JSONObject.NULL);
        assertNull(Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T00:59:59")));
        assertEquals(LocalDateTime.parse("2026-10-17T01:00"),Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T01:30")));
        assertEquals(LocalDateTime.parse("2026-10-17T02:00"),Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T02:00:03")));
        assertNull(Deadline.slot(task,row,LocalDateTime.parse("2026-10-17T03:00")));
        Deadline.decorate(task,row,LocalDateTime.parse("2026-10-17T01:59"));
        assertTrue(row.getBoolean("in_grace"));assertTrue(row.getBoolean("can_dismiss"));
        assertFalse(row.getBoolean("in_unfinished"));
        Deadline.decorate(task,row,LocalDateTime.parse("2026-10-17T02:00"));
        assertFalse(row.getBoolean("in_grace"));assertTrue(row.getBoolean("can_dismiss"));
        assertTrue(row.getBoolean("in_unfinished"));
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
