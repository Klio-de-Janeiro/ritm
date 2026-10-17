package ru.klio.ritm;
import org.json.*;
import org.junit.Test;
import java.time.*;
import static org.junit.Assert.*;

public class CheckinsTest {
    @Test public void missedSlotsCanBeChangedAndFutureSlotsStayLocked() throws Exception {
        JSONObject task=Model.clean(new JSONObject().put("title","спорт").put("recurrence","every_3_hours").put("time","09:00").put("reminder",true),false);
        JSONObject row=Model.occurrence(task.put("id","sport"),"2026-10-18");
        Checkins.decorate(task,row,LocalDateTime.parse("2026-10-18T12:00"));
        assertEquals(5,row.getJSONArray("slots").length());
        assertEquals("missed",row.getJSONArray("checkins").getJSONObject(0).getString("status"));
        assertEquals("pending",row.getJSONArray("checkins").getJSONObject(1).getString("status"));
        assertFalse(row.getJSONArray("checkins").getJSONObject(2).getBoolean("available"));
        Checkins.apply(row,"09:00",true,"2026-10-18T12:00:00+03:00");
        Checkins.apply(row,"09:00",true,"2026-10-18T12:01:00+03:00");
        assertEquals(1,row.getJSONObject("checks").length());
        Checkins.apply(row,"09:00",false,JSONObject.NULL);
        assertEquals(0,row.getJSONObject("checks").length());
        assertEquals("Спорт",task.getString("title"));
    }
    @Test public void offlineSlotOperationsReplayIndependently() throws Exception {
        JSONObject data=Model.empty(),task=Model.clean(new JSONObject().put("title","Спорт").put("recurrence","every_3_hours").put("time","09:00").put("reminder",true),false).put("id","sport").put("archived_date",JSONObject.NULL);
        String date=LocalDate.now().minusDays(1).toString();task.put("start_date",date);data.getJSONArray("tasks").put(task);
        for(String time:new String[]{"09:00","12:00"}) Model.mutate(data,new JSONObject().put("kind","check").put("task_id","sport").put("date",date).put("time",time).put("completed",true));
        JSONObject view=Model.view(data,date);JSONObject row=Model.map(view.getJSONArray("occurrences"),true).get("sport|"+date);
        assertEquals(2,row.getJSONObject("checks").length());assertEquals("pending",row.getString("status"));
    }
}
