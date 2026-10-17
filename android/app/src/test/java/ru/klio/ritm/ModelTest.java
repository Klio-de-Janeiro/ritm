package ru.klio.ritm;
import org.junit.Test;
import org.json.*;
import java.time.LocalDate;
import java.util.UUID;
import static org.junit.Assert.*;

/** Эти проверки запускает BUILD_ANDROID до создания APK. */
public class ModelTest {
    @Test public void onceReminderDefaultsToOneDayAndLegacyOnceAlsoExpires() throws Exception {
        LocalDate day=LocalDate.now();
        JSONObject task=Model.clean(new JSONObject().put("title","Лекция по физике").put("reminder",true).put("time","21:00"),false);
        assertEquals(day.toString(),task.getString("reminder_end_date"));
        assertTrue(Model.remindersActive(task,day));
        assertFalse(Model.remindersActive(task,day.plusDays(1)));
        task.put("reminder_end_date",JSONObject.NULL);
        assertFalse(Model.remindersActive(task,day.plusDays(1)));
    }
    @Test public void missedOfflineDaysAreMaterializedWithoutLosingCompletions() throws Exception {
        JSONObject data=Model.empty();
        JSONObject task=new JSONObject().put("id",UUID.randomUUID().toString()).put("title","Спорт").put("notes","")
            .put("recurrence","daily").put("start_date",LocalDate.now().minusDays(2).toString()).put("archived_date",JSONObject.NULL)
            .put("category","Здоровье").put("priority","normal").put("time","09:00").put("reminder",true);
        data.getJSONArray("tasks").put(task);
        JSONObject restored=Model.view(new JSONObject(data.toString()),LocalDate.now().toString());
        assertEquals(3,restored.getJSONArray("occurrences").length());
        assertEquals("pending",restored.getJSONArray("occurrences").getJSONObject(0).getString("status"));
        Model.mutate(data,new JSONObject().put("kind","complete").put("task_id",task.getString("id"))
            .put("date",LocalDate.now().minusDays(2).toString()).put("completed",true));
        JSONObject view=Model.view(new JSONObject(data.toString()),LocalDate.now().toString());
        int completed=0;for(int i=0;i<view.getJSONArray("occurrences").length();i++) if(view.getJSONArray("occurrences").getJSONObject(i).getString("status").equals("done")) completed++;
        assertEquals(1,completed);
    }
    @Test public void reminderDefaultsCutoffAndEventEscalation() throws Exception {
        LocalDate today=LocalDate.now(),event=today.plusDays(4);
        JSONObject task=Model.clean(new JSONObject().put("title","Событие").put("reminder",true)
            .put("recurrence","event").put("event_date",event.toString()).put("escalation_days",3),false)
            .put("archived_date",JSONObject.NULL);
        assertEquals("09:00",task.getString("time"));
        assertArrayEquals(new String[]{"09:00"},Model.dailyTimes(task,today));
        assertArrayEquals(new String[]{"09:00","15:00","21:00"},Model.dailyTimes(task,today.plusDays(1)));
        assertTrue(Model.scheduled(task,event.toString()));
        assertFalse(Model.scheduled(task,event.plusDays(1).toString()));
        assertFalse(Model.remindersActive(task,event.plusDays(1)));
        task.put("reminder_end_date",today.toString());
        assertTrue(Model.remindersActive(task,today));
        assertFalse(Model.remindersActive(task,today.plusDays(1)));
        assertTrue(Model.scheduled(task,today.plusDays(1).toString()));
        task.put("recurrence","three_times_daily");
        assertArrayEquals(new String[]{"09:00","15:00","21:00"},Model.dailyTimes(task,today));
    }
    @Test public void eventOptionsSurviveOfflineEditAndRestart() throws Exception {
        JSONObject data=Model.empty(),task=create(data,"daily");
        JSONObject values=Model.fields(task).put("recurrence","event").put("reminder",true).put("time","09:00")
            .put("event_date",LocalDate.now().plusDays(4).toString()).put("reminder_end_date",LocalDate.now().plusDays(3).toString()).put("escalation_days",2);
        Model.mutate(data,new JSONObject().put("kind","edit").put("task_id",task.getString("id")).put("values",values));
        JSONObject restored=Model.view(new JSONObject(data.toString()),LocalDate.now().toString()).getJSONArray("tasks").getJSONObject(0);
        assertEquals(values.getString("event_date"),restored.getString("event_date"));
        assertEquals(values.getString("reminder_end_date"),restored.getString("reminder_end_date"));
        assertEquals(2,restored.getInt("escalation_days"));
    }
    @Test public void invalidEventSettingsAreRejected() throws Exception {
        for(JSONObject values:new JSONObject[]{new JSONObject().put("recurrence","event"),
            new JSONObject().put("escalation_days",0),new JSONObject().put("escalation_days",366),
            new JSONObject().put("escalation_days",true),new JSONObject().put("reminder_end_date",LocalDate.now().minusDays(1).toString())}) {
            try { Model.clean(values.put("title","Проверка").put("reminder",true),false);fail("Expected validation error"); }
            catch(IllegalArgumentException expected) {}
        }
    }
    JSONObject create(JSONObject data,String recurrence) throws Exception {
        Model.mutate(data,new JSONObject().put("kind","create").put("values",new JSONObject()
            .put("title","Прогулка").put("recurrence",recurrence).put("start_date",LocalDate.now().toString())));
        return Model.view(data,LocalDate.now().toString()).getJSONArray("tasks").getJSONObject(0);
    }
    JSONObject complete(JSONObject task,boolean value) throws Exception {
        return new JSONObject().put("kind","complete").put("task_id",task.getString("id"))
            .put("date",LocalDate.now().toString()).put("completed",value);
    }
    JSONObject response(JSONObject data,JSONArray accepted,JSONArray conflicts) throws Exception {
        JSONObject view=Model.view(data,LocalDate.now().toString());
        return new JSONObject().put("protocol",2).put("server_id",UUID.randomUUID().toString())
            .put("tasks",view.getJSONArray("tasks")).put("occurrences",view.getJSONArray("occurrences"))
            .put("accepted",accepted).put("conflicts",conflicts);
    }
    @Test public void offlineCreateCompleteAndRestart() throws Exception {
        JSONObject data=Model.empty(),task=create(data,"daily");Model.mutate(data,complete(task,true));
        JSONObject restored=new JSONObject(data.toString()),view=Model.view(restored,LocalDate.now().toString());
        assertEquals("done",view.getJSONArray("occurrences").getJSONObject(0).getString("status"));
        assertEquals(2,restored.getJSONArray("pending").length());
        JSONObject tomorrow=Model.view(restored,LocalDate.now().plusDays(1).toString());
        assertEquals(2,tomorrow.getJSONArray("occurrences").length());
    }
    @Test public void repeatedCompletionDoesNotAddAnotherOperation() throws Exception {
        JSONObject data=Model.empty(),task=create(data,"once");Model.mutate(data,complete(task,true));Model.mutate(data,complete(task,true));
        assertEquals(2,data.getJSONArray("pending").length());Model.mutate(data,complete(task,false));
        assertEquals("pending",Model.view(data,LocalDate.now().toString()).getJSONArray("occurrences").getJSONObject(0).getString("status"));
        String completed=data.getJSONArray("pending").getJSONObject(1).getString("completed_at");
        assertEquals(completed,data.getJSONArray("pending").getJSONObject(2).getJSONObject("before").getString("completed_at"));
        assertTrue(completed.matches(".*[+-][0-9]{2}:[0-9]{2}"));
    }
    @Test public void ackDoesNotDiscardEditMadeDuringNetworkRequest() throws Exception {
        JSONObject data=Model.empty(),task=create(data,"once"),sentState=Model.copy(data);
        JSONArray sent=new JSONArray(data.getJSONArray("pending").toString());
        JSONObject response=response(data,new JSONArray().put(sent.getJSONObject(0).getString("id")),new JSONArray());
        JSONObject values=Model.fields(task).put("title","Новое название");
        Model.mutate(data,new JSONObject().put("kind","edit").put("task_id",task.getString("id")).put("values",values));
        Model.accept(data,response,sent);
        assertEquals(1,data.getJSONArray("pending").length());
        assertEquals("Новое название",Model.view(data,LocalDate.now().toString()).getJSONArray("tasks").getJSONObject(0).getString("title"));
        assertEquals(1,sentState.getJSONArray("pending").length());
    }
    @Test public void conflictPreservesPhoneContentUntilExplicitChoice() throws Exception {
        JSONObject data=Model.empty(),task=create(data,"once");
        JSONArray sent=new JSONArray(data.getJSONArray("pending").toString());String id=sent.getJSONObject(0).getString("id");
        JSONObject response=response(Model.empty(),new JSONArray(),new JSONArray().put(new JSONObject().put("id",id).put("reason","Конфликт")));
        Model.accept(data,response,sent);
        assertEquals(0,data.getJSONArray("pending").length());assertEquals(1,data.getJSONArray("conflicts").length());
        assertEquals("Прогулка",data.getJSONArray("conflicts").getJSONObject(0).getJSONObject("operation").getJSONObject("values").getString("title"));
        Model.resolve(data,0,true);assertEquals(1,data.getJSONArray("pending").length());assertEquals(0,data.getJSONArray("conflicts").length());
    }
    @Test public void incompleteReceiptLeavesQueueUntouched() throws Exception {
        JSONObject data=Model.empty();create(data,"once");String old=data.toString();
        JSONObject response=response(data,new JSONArray(),new JSONArray());
        try {Model.accept(data,response,data.getJSONArray("pending"));fail("Expected incomplete receipt error");} catch(IllegalArgumentException expected) {}
        assertEquals(old,data.toString());
    }
    @Test public void completedSnapshotSurvivesTitleEdit() throws Exception {
        JSONObject data=Model.empty(),task=create(data,"daily");Model.mutate(data,complete(task,true));
        Model.mutate(data,new JSONObject().put("kind","edit").put("task_id",task.getString("id")).put("values",Model.fields(task).put("title","Новое название")));
        JSONObject view=Model.view(data,LocalDate.now().toString());
        assertEquals("Прогулка",view.getJSONArray("occurrences").getJSONObject(0).getString("title"));
        assertEquals("Новое название",view.getJSONArray("tasks").getJSONObject(0).getString("title"));
    }
    @Test public void assistantPlanValidatesEveryTaskBeforeMutation() throws Exception {
        JSONObject body=new JSONObject().put("tasks",new JSONArray().put(new JSONObject().put("title","Да")).put(new JSONObject().put("title","")));
        try {Model.plan(body);fail("Expected validation error");} catch(IllegalArgumentException expected) {}
        body=new JSONObject().put("tasks",new JSONArray().put(new JSONObject().put("title","Шаг")));
        assertEquals(1,Model.plan(body).length());
    }
    @Test public void recurrenceUsesCalendarWeekdayAndArchiveBoundary() throws Exception {
        JSONObject task=Model.clean(new JSONObject().put("title","Учёба").put("recurrence","weekdays"),false).put("start_date","2026-10-12").put("archived_date",JSONObject.NULL);
        assertTrue(Model.scheduled(task,"2026-10-16"));assertFalse(Model.scheduled(task,"2026-10-17"));
        task.put("recurrence","weekly");assertTrue(Model.scheduled(task,"2026-10-19"));assertFalse(Model.scheduled(task,"2026-10-20"));
        task.put("archived_date","2026-10-19");assertFalse(Model.scheduled(task,"2026-10-19"));
    }
    @Test public void fiveHourSlotsContinueAcrossMidnight() throws Exception {
        JSONObject task=new JSONObject().put("recurrence","every_5_hours").put("start_date","2026-10-16").put("time","09:00");
        assertEquals("2026-10-17T00:00",Model.hourlySlot(task,java.time.LocalDateTime.parse("2026-10-17T01:00")).toString());
        assertEquals("2026-10-17T05:00",Model.nextHourlySlot(task,java.time.LocalDateTime.parse("2026-10-17T01:00")).toString());
        assertNull(Model.hourlySlot(task,java.time.LocalDateTime.parse("2026-10-16T08:00")));
    }
    @Test public void threeDaysAndMonthlyLastDayKeepTheirAnchor() throws Exception {
        JSONObject task=new JSONObject().put("start_date","2026-01-31").put("recurrence","monthly").put("archived_date",JSONObject.NULL);
        assertTrue(Model.scheduled(task,"2026-02-28"));assertTrue(Model.scheduled(task,"2026-03-31"));assertFalse(Model.scheduled(task,"2026-03-28"));
        task.put("recurrence","every_3_days").put("start_date","2026-10-16");
        assertTrue(Model.scheduled(task,"2026-10-19"));assertFalse(Model.scheduled(task,"2026-10-20"));
    }
}
