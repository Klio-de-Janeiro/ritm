package ru.klio.ritm;
import org.json.*;
import java.time.*;

final class Checkins {
    static JSONArray plan(JSONObject task) throws Exception {
        int hours=Model.hourInterval(task.getString("recurrence"));
        if(hours==0 || task.isNull("time")) return null;
        LocalTime start=LocalTime.parse(task.getString("time"));
        String end=task.isNull("repeat_until")?(start.isAfter(LocalTime.of(21,0))?start.toString():"21:00"):task.getString("repeat_until");
        JSONArray result=new JSONArray();
        for(int minute=start.toSecondOfDay()/60;minute<=LocalTime.parse(end).toSecondOfDay()/60;minute+=hours*60)
            result.put(String.format(java.util.Locale.ROOT,"%02d:%02d",minute/60,minute%60));
        return result;
    }
    static void decorate(JSONObject task,JSONObject row,LocalDateTime now) throws Exception {
        if(row.isNull("slots") && row.getString("status").equals("pending")) row.put("slots",plan(task));
        if(row.isNull("checks")) row.put("checks",new JSONObject());
        JSONArray slots=row.optJSONArray("slots"),items=new JSONArray();JSONObject checks=row.getJSONObject("checks");
        if(slots!=null) for(int i=0;i<slots.length();i++) {
            String time=slots.getString(i);
            LocalDateTime at=LocalDateTime.parse(row.getString("date")+"T"+time);
            LocalDateTime until=i+1<slots.length()?LocalDateTime.parse(row.getString("date")+"T"+slots.getString(i+1)):LocalDate.parse(row.getString("date")).plusDays(1).atStartOfDay();
            if(i+1==slots.length() && at.plusHours(Model.hourInterval(task.getString("recurrence"))).isBefore(until)) until=at.plusHours(Model.hourInterval(task.getString("recurrence")));
            String status=checks.has(time)?"done":now.isBefore(at)?"future":!now.isBefore(until)?"missed":"pending";
            items.put(new JSONObject().put("time",time).put("status",status).put("available",!now.isBefore(at)&&row.isNull("dismissed_at"))
                .put("completed_at",checks.opt(time)==null?JSONObject.NULL:checks.opt(time)));
        }
        row.put("checkins",items);
    }
    static void apply(JSONObject row,String time,boolean completed,Object at) throws Exception {
        JSONObject checks=row.optJSONObject("checks");if(checks==null) checks=new JSONObject();
        if(completed) {if(!checks.has(time)) checks.put(time,at);} else checks.remove(time);
        JSONArray slots=row.optJSONArray("slots");
        boolean done=slots!=null && checks.length()==slots.length();
        String latest="";for(java.util.Iterator<String> it=checks.keys();it.hasNext();) {String value=checks.getString(it.next());if(value.compareTo(latest)>0) latest=value;}
        row.put("checks",checks).put("status",done?"done":"pending").put("completed_at",done?latest:JSONObject.NULL);
    }
}
