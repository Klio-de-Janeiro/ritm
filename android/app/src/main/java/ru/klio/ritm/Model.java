package ru.klio.ritm;

import org.json.JSONArray;
import org.json.JSONObject;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.*;

/** Чистая модель: снимок ноутбука + долговечная очередь локальных операций. */
public final class Model {
    static final String[] FIELDS = {"title","notes","category","priority","recurrence","start_date","time","reminder","reminder_end_date","event_date","escalation_days","repeat_until","checkins_enabled"};
    static final String[] SNAPSHOT = {"title","notes","category","priority","time","reminder"};
    private Model() {}
    static int hourInterval(String recurrence) {
        switch(recurrence) {
            case "hourly": return 1;
            case "every_3_hours": return 3;
            case "every_5_hours": return 5;
            case "every_8_hours": return 8;
            case "every_12_hours": return 12;
            default: return 0;
        }
    }
    public static String[] dailyTimes(JSONObject task, LocalDate day) throws Exception {
        String mode=task.getString("recurrence");
        if(mode.equals("three_times_daily")) return new String[]{"09:00","15:00","21:00"};
        if(mode.equals("event")) {
            long remaining=java.time.temporal.ChronoUnit.DAYS.between(day,LocalDate.parse(task.getString("event_date")));
            return remaining>=0 && remaining<=task.optInt("escalation_days",3)
                ? new String[]{"09:00","15:00","21:00"} : new String[]{"09:00"};
        }
        return new String[0];
    }
    public static boolean remindersActive(JSONObject task, LocalDate day) throws Exception {
        if(task.getString("recurrence").equals("once") && task.isNull("reminder_end_date") && day.isAfter(LocalDate.parse(task.getString("start_date")))) return false;
        if(!task.isNull("reminder_end_date") && day.isAfter(LocalDate.parse(task.getString("reminder_end_date")))) return false;
        return !task.getString("recurrence").equals("event") || !day.isAfter(LocalDate.parse(task.getString("event_date")));
    }
    public static java.time.LocalDateTime hourlySlot(JSONObject task,java.time.LocalDateTime now) throws Exception {
        int hours=hourInterval(task.getString("recurrence"));
        if(hours==0) return null;
        java.time.LocalDateTime anchor=java.time.LocalDateTime.parse(task.getString("start_date")+"T"+task.getString("time"));
        if(now.isBefore(anchor)) return null;
        long slots=java.time.Duration.between(anchor,now).getSeconds()/(hours*3600L);
        java.time.LocalDateTime slot=anchor.plusHours(slots*hours);
        return slot.toLocalDate().equals(now.toLocalDate())?slot:null;
    }
    public static java.time.LocalDateTime nextHourlySlot(JSONObject task,java.time.LocalDateTime now) throws Exception {
        int hours=hourInterval(task.getString("recurrence"));
        java.time.LocalDateTime anchor=java.time.LocalDateTime.parse(task.getString("start_date")+"T"+task.getString("time"));
        if(now.isBefore(anchor)) return anchor;
        long slots=java.time.Duration.between(anchor,now).getSeconds()/(hours*3600L);
        return anchor.plusHours((slots+1)*hours);
    }
    public static JSONObject copy(JSONObject value) throws Exception { return new JSONObject(value.toString()); }
    static Object nullable(JSONObject value, String key) { return value.has(key) ? value.opt(key) : JSONObject.NULL; }
    static boolean equal(Object a, Object b) { return Objects.equals(a, b); }
    static JSONObject device(JSONObject data,String name) throws Exception {
        if(data.optString("device_id").isEmpty()) data.put("device_id",UUID.randomUUID().toString());
        String label=name==null?"Android":name.replaceAll("[\\p{Cntrl}]", " ").trim();
        if(label.isEmpty()) label="Android";
        if(label.length()>80) label=label.substring(0,80);
        return new JSONObject().put("id",data.getString("device_id")).put("name",label);
    }
    public static JSONObject empty() throws Exception {
        return new JSONObject().put("schema",1).put("server_id", "")
            .put("tasks",new JSONArray()).put("occurrences",new JSONArray())
            .put("pending",new JSONArray()).put("conflicts",new JSONArray())
            .put("connection",new JSONObject()).put("settings",new JSONObject()
                .put("hue",190).put("brightness",6).put("notifications",true))
            .put("delivered",new JSONObject()).put("last_sync","").put("sync_error","");
    }
    public static boolean scheduled(JSONObject task, String date) throws Exception {
        LocalDate day = LocalDate.parse(date), start = LocalDate.parse(task.getString("start_date"));
        if(!task.isNull("occurrence_end_date") && date.compareTo(task.getString("occurrence_end_date"))>0) return false;
        String archived = task.optString("archived_date", "");
        if (day.isBefore(start) || (!archived.isEmpty() && date.compareTo(archived)>=0)) return false;
        switch (task.getString("recurrence")) {
            case "event": return !day.isAfter(LocalDate.parse(task.getString("event_date")));
            case "once": return day.equals(start);
            case "weekly": return day.getDayOfWeek()==start.getDayOfWeek();
            case "weekdays": return day.getDayOfWeek().getValue()<=5;
            case "every_3_days": return java.time.temporal.ChronoUnit.DAYS.between(start,day)%3==0;
            case "monthly": return day.getDayOfMonth()==Math.min(start.getDayOfMonth(),day.lengthOfMonth());
            default: return true;
        }
    }
    static String key(JSONObject item) throws Exception { return item.getString("task_id")+"|"+item.getString("date"); }
    static JSONObject occurrence(JSONObject task, String day) throws Exception {
        JSONObject row = new JSONObject().put("task_id",task.getString("id")).put("date",day)
            .put("status","pending").put("completed_at",JSONObject.NULL).put("dismissed_at",JSONObject.NULL);
        for (String field:SNAPSHOT) row.put(field,nullable(task,field));
        row.put("slots",Checkins.plan(task)).put("checks",new JSONObject());
        return row;
    }
    static LinkedHashMap<String,JSONObject> map(JSONArray array, boolean occurrences) throws Exception {
        LinkedHashMap<String,JSONObject> result = new LinkedHashMap<>();
        for(int i=0;i<array.length();i++) { JSONObject item=copy(array.getJSONObject(i)); result.put(occurrences?key(item):item.getString("id"),item); }
        return result;
    }
    static void materialize(Map<String,JSONObject> tasks, Map<String,JSONObject> occurrences, String day) throws Exception {
        for(JSONObject task:tasks.values()) if(scheduled(task,day)) {
            String key=task.getString("id")+"|"+day;
            if(!occurrences.containsKey(key)) occurrences.put(key,occurrence(task,day));
        }
    }
    static void materializeHistory(JSONObject task,Map<String,JSONObject> rows,LocalDate end) throws Exception {
        LocalDate first=LocalDate.parse(task.getString("start_date"));
        if(task.getString("recurrence").equals("once") && first.isBefore(end)) end=first;
        if(!task.isNull("archived_date")) {
            LocalDate archived=LocalDate.parse(task.getString("archived_date")).minusDays(1);
            if(archived.isBefore(end)) end=archived;
        }
        for(LocalDate date=first;!date.isAfter(end);date=date.plusDays(1)) {
            String value=date.toString(),key=task.getString("id")+"|"+value;
            if(!rows.containsKey(key) && scheduled(task,value)) rows.put(key,occurrence(task,value));
        }
    }
    /** Воспроизведение очереди не меняет исходный снимок и не теряет новые правки. */
    public static JSONObject view(JSONObject data, String day) throws Exception {
        LinkedHashMap<String,JSONObject> tasks=map(data.getJSONArray("tasks"),false);
        for(JSONObject task:tasks.values()) if(!task.has("checkins_enabled")) task.put("checkins_enabled",false);
        LinkedHashMap<String,JSONObject> rows=map(data.getJSONArray("occurrences"),true);
        JSONArray pending=data.getJSONArray("pending");
        String today=LocalDate.now().toString();
        for(int i=0;i<pending.length();i++) {
            JSONObject op=pending.getJSONObject(i); String id=op.getString("task_id"), kind=op.getString("kind");
            JSONObject task=tasks.get(id);
            if(kind.equals("create")) {
                task=copy(op.getJSONObject("values")).put("id",id).put("archived_date",JSONObject.NULL);
                tasks.put(id,task);
            } else if(task!=null && kind.equals("edit")) {
                materializeHistory(task,rows,LocalDate.parse(today));
                JSONObject values=op.getJSONObject("values");
                for(Iterator<String> it=values.keys();it.hasNext();) {String field=it.next();task.put(field,values.get(field));}
                for(JSONObject row:rows.values()) if(row.getString("task_id").equals(id) && !row.getString("status").equals("done") && row.getString("date").compareTo(today)>=0) {
                    if(row.optJSONObject("checks")!=null && row.getJSONObject("checks").length()>0) continue;
                    for(String field:SNAPSHOT) row.put(field,nullable(task,field));
                    row.put("status",scheduled(task,row.getString("date"))?"pending":"cancelled");
                    row.put("slots",Checkins.plan(task)).put("checks",new JSONObject());
                }
            } else if(task!=null && kind.equals("archive")) {
                String archive=op.getString("date"); task.put("archived_date",archive);
                for(JSONObject row:rows.values()) if(row.getString("task_id").equals(id) && row.getString("date").compareTo(archive)>=0 && row.getString("status").equals("pending")) row.put("status","cancelled");
            } else if(task!=null && kind.equals("check")) {
                String date=op.getString("date"),key=id+"|"+date;
                JSONObject row=rows.get(key);if(row==null) {row=occurrence(task,date);rows.put(key,row);}
                if(row.isNull("slots")) row.put("slots",Checkins.plan(task));
                Checkins.apply(row,op.getString("time"),op.getBoolean("completed"),op.opt("completed_at"));
            } else if(task!=null && kind.equals("dismiss")) {
                String date=op.getString("date"),key=id+"|"+date;
                JSONObject row=rows.get(key);
                if(row==null) {row=occurrence(task,date);rows.put(key,row);}
                row.put("dismissed_at",nullable(op,"dismissed_at"));
            } else if(task!=null && kind.equals("complete")) {
                String date=op.getString("date"), key=id+"|"+date;
                JSONObject row=rows.get(key);
                if(row==null) {row=occurrence(task,date);rows.put(key,row);}
                row.put("status",op.getBoolean("completed")?"done":"pending");
                row.put("completed_at",op.getBoolean("completed")?op.get("completed_at"):JSONObject.NULL);
            }
            if(task!=null && !task.optString("start_date").isEmpty() && task.optString("recurrence").equals("once"))
                materialize(tasks,rows,task.getString("start_date"));
        }
        materialize(tasks,rows,day);
        // Восстановить дни, проведённые офлайн; уже сохранённые снимки не заменяются.
        for(JSONObject task:tasks.values()) materializeHistory(task,rows,LocalDate.parse(today));
        for(JSONObject task:tasks.values()) if(task.optString("recurrence").equals("once")) materialize(tasks,rows,task.getString("start_date"));
        for(JSONObject row:rows.values()) {
            JSONObject task=tasks.get(row.getString("task_id"));
            if(task!=null) {Deadline.decorate(task,row,java.time.LocalDateTime.now());Checkins.decorate(task,row,java.time.LocalDateTime.now());}
        }
        return new JSONObject().put("tasks",new JSONArray(tasks.values())).put("occurrences",new JSONArray(rows.values()))
            .put("pending_count",pending.length()).put("conflicts",data.getJSONArray("conflicts"))
            .put("settings",data.getJSONObject("settings")).put("last_sync",data.optString("last_sync"))
            .put("sync_error",data.optString("sync_error")).put("connected",!data.getJSONObject("connection").optString("address").isEmpty())
            .put("sync_transport",data.optString("sync_transport"))
            .put("address",data.getJSONObject("connection").optString("address"));
    }
    public static JSONObject clean(JSONObject source, boolean allowPast) throws Exception {
        Set<String> fields=new HashSet<>(Arrays.asList(FIELDS));
        for(Iterator<String> it=source.keys();it.hasNext();) if(!fields.contains(it.next())) throw new IllegalArgumentException("Неизвестное поле задачи.");
        JSONObject task=new JSONObject().put("title","").put("notes","").put("category","Личное")
            .put("priority","normal").put("recurrence","once").put("start_date",LocalDate.now().toString())
            .put("time",JSONObject.NULL).put("reminder",false);
        task.put("reminder_end_date",JSONObject.NULL).put("event_date",JSONObject.NULL).put("escalation_days",3).put("repeat_until",JSONObject.NULL).put("checkins_enabled",false);
        for(String field:FIELDS) if(source.has(field)) task.put(field,source.get(field));
        for(String field:new String[]{"title","notes"}) {
            if(!(task.get(field) instanceof String)) throw new IllegalArgumentException("Название и заметка должны быть текстом.");
            task.put(field,task.getString(field).trim());
            if(field.equals("title") && !task.getString(field).isEmpty()) {String title=task.getString(field);task.put(field,title.substring(0,1).toUpperCase(java.util.Locale.ROOT)+title.substring(1));}
        }
        if(task.getString("title").isEmpty() || task.getString("title").length()>200 || task.getString("notes").length()>2000) throw new IllegalArgumentException("Название: 1–200 символов. Заметка: до 2000.");
        if(!Arrays.asList("Работа","Учёба","Здоровье","Личное").contains(task.getString("category"))) throw new IllegalArgumentException("Неизвестная категория.");
        if(!Arrays.asList("low","normal","high").contains(task.getString("priority"))) throw new IllegalArgumentException("Неизвестный приоритет.");
        if(!Arrays.asList("once","hourly","every_3_hours","every_5_hours","every_8_hours","every_12_hours","daily","every_3_days","weekdays","weekly","monthly","three_times_daily","event").contains(task.getString("recurrence"))) throw new IllegalArgumentException("Неизвестное повторение.");
        LocalDate start=LocalDate.parse(task.getString("start_date"));
        if(!start.toString().equals(task.getString("start_date")) || (!allowPast && start.isBefore(LocalDate.now())) || start.isAfter(LocalDate.now().plusDays(3650))) throw new IllegalArgumentException("Проверь дату начала задачи.");
        if(!(task.get("reminder") instanceof Boolean)) throw new IllegalArgumentException("Некорректный переключатель напоминания.");
        if(task.getString("recurrence").equals("once") && task.isNull("reminder_end_date")) task.put("reminder_end_date",start.toString());
        for(String field:new String[]{"reminder_end_date","event_date"}) {
            if(task.optString(field).isEmpty()) task.put(field,JSONObject.NULL);
            if(!task.isNull(field)) {
                LocalDate end=LocalDate.parse(task.getString(field));
                if(!end.toString().equals(task.getString(field)) || end.isBefore(start) || end.isAfter(LocalDate.now().plusDays(3650))) throw new IllegalArgumentException("Дата окончания или события должна быть не раньше начала и в пределах 10 лет.");
            }
        }
        Object days=task.get("escalation_days");
        if(!(days instanceof Integer || days instanceof Long) || task.getLong("escalation_days")<1 || task.getLong("escalation_days")>365) throw new IllegalArgumentException("Учащение: от 1 до 365 дней.");
        if(task.getString("recurrence").equals("event") && task.isNull("event_date")) throw new IllegalArgumentException("Укажи дату события.");
        if(task.getBoolean("reminder") && !source.has("time")) task.put("time","09:00");
        if(Arrays.asList("event","three_times_daily").contains(task.getString("recurrence"))) {
            if(!task.getBoolean("reminder")) throw new IllegalArgumentException("Включи напоминание для этого режима.");
            task.put("time","09:00");
        }
        if(task.optString("time").isEmpty()) task.put("time",JSONObject.NULL);
        if(!task.isNull("time") && !task.getString("time").matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]")) throw new IllegalArgumentException("Время: ЧЧ:ММ.");
        if(!(task.get("checkins_enabled") instanceof Boolean)) throw new IllegalArgumentException("Некорректный переключатель подходов.");
        if(hourInterval(task.getString("recurrence"))==0) task.put("checkins_enabled",false);
        if(task.getBoolean("checkins_enabled")) {
            String timeStart=task.optString("time","09:00"),end=task.isNull("repeat_until")?(timeStart.compareTo("21:00")>0?timeStart:"21:00"):task.getString("repeat_until");
            if(!end.matches("(?:[01][0-9]|2[0-3]):[0-5][0-9]") || end.compareTo(timeStart)<0) throw new IllegalArgumentException("Конец подходов должен быть не раньше начала.");
            task.put("repeat_until",end);
        } else task.put("repeat_until",JSONObject.NULL);
        if(task.getBoolean("reminder") && task.isNull("time")) throw new IllegalArgumentException("Для напоминания укажи время.");
        if(hourInterval(task.getString("recurrence"))>0 && !task.getBoolean("reminder")) throw new IllegalArgumentException("Для часового повтора включи напоминание и укажи время.");
        return task;
    }
    static JSONObject task(JSONObject view,String id) throws Exception {
        JSONArray tasks=view.getJSONArray("tasks");
        for(int i=0;i<tasks.length();i++) if(tasks.getJSONObject(i).getString("id").equals(id)) return tasks.getJSONObject(i);
        throw new IllegalArgumentException("Задача не найдена.");
    }
    static JSONObject fields(JSONObject task) throws Exception {
        JSONObject result=new JSONObject(); for(String key:FIELDS) result.put(key,key.equals("checkins_enabled")?task.optBoolean(key,false):key.equals("escalation_days")?task.optInt(key,3):nullable(task,key)); return result;
    }
    /** Добавить команду, сохраняя значения, на основе которых пользователь правил задачу. */
    public static void mutate(JSONObject data, JSONObject command) throws Exception {
        JSONArray queue=data.getJSONArray("pending");
        if(queue.length()>=5000) throw new IllegalArgumentException("Сначала синхронизируй накопленные изменения.");
        String kind=command.getString("kind"), day=command.optString("date",LocalDate.now().toString());
        JSONObject op=new JSONObject().put("id",UUID.randomUUID().toString()).put("kind",kind);
        if(kind.equals("create")) {
            op.put("task_id",UUID.randomUUID().toString()).put("values",clean(command.getJSONObject("values"),false));
        } else {
            JSONObject view=view(data,day); String id=command.getString("task_id"); JSONObject task=task(view,id);
            if(!task.isNull("archived_date") && !Arrays.asList("complete","dismiss","check").contains(kind)) throw new IllegalArgumentException("Задача уже в архиве.");
            op.put("task_id",id);
            if(kind.equals("edit")) {
                JSONObject checked=clean(command.getJSONObject("values"),true), before=new JSONObject(), changed=new JSONObject();
                if(!checked.getString("start_date").equals(task.getString("start_date")) && LocalDate.parse(checked.getString("start_date")).isBefore(LocalDate.now())) throw new IllegalArgumentException("Новая дата не может быть в прошлом.");
                JSONObject original=fields(task);
                for(String field:FIELDS) if(!equal(nullable(original,field),nullable(checked,field))) {before.put(field,nullable(original,field));changed.put(field,nullable(checked,field));}
                if(changed.length()==0) return;
                op.put("values",changed).put("before",before);
            } else if(kind.equals("archive")) {
                op.put("before",fields(task)).put("date",LocalDate.now().toString());
            } else if(kind.equals("check")) {
                JSONObject row=map(view.getJSONArray("occurrences"),true).get(id+"|"+day);
                String time=command.getString("time");JSONArray slots=row==null?null:row.optJSONArray("slots");
                boolean found=false;if(slots!=null) for(int n=0;n<slots.length();n++) if(slots.getString(n).equals(time)) found=true;
                if(!found || !row.isNull("dismissed_at") || row.getString("status").equals("cancelled")) throw new IllegalArgumentException("Подход недоступен.");
                if(java.time.LocalDateTime.parse(day+"T"+time).isAfter(java.time.LocalDateTime.now())) throw new IllegalArgumentException("Будущий подход пока нельзя отметить.");
                boolean completed=command.getBoolean("completed");
                Object before=row.getJSONObject("checks").opt(time);
                op.put("date",day).put("time",time).put("completed",completed)
                    .put("before",before==null?JSONObject.NULL:before)
                    .put("completed_at",completed?OffsetDateTime.now().truncatedTo(java.time.temporal.ChronoUnit.SECONDS).format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")):JSONObject.NULL);
            } else if(kind.equals("dismiss")) {
                JSONObject row=map(view.getJSONArray("occurrences"),true).get(id+"|"+day);
                if(row==null || !row.getString("status").equals("pending")) throw new IllegalArgumentException("Дело уже выполнено или отменено.");
                boolean dismissed=command.getBoolean("dismissed");
                if(dismissed==!row.isNull("dismissed_at")) return;
                if(dismissed && !row.optBoolean("can_dismiss")) throw new IllegalArgumentException("Будущее дело пока нельзя зачеркнуть.");
                op.put("date",day).put("dismissed",dismissed)
                    .put("dismissed_at",dismissed?OffsetDateTime.now().truncatedTo(java.time.temporal.ChronoUnit.SECONDS)
                        .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")):JSONObject.NULL)
                    .put("before",new JSONObject().put("status",row.getString("status")).put("dismissed_at",nullable(row,"dismissed_at")));
            } else if(kind.equals("complete")) {
                if(LocalDate.parse(day).isAfter(LocalDate.now())) throw new IllegalArgumentException("Будущую задачу пока нельзя выполнить.");
                JSONObject row=map(view.getJSONArray("occurrences"),true).get(id+"|"+day);
                if(row==null || row.getString("status").equals("cancelled")) throw new IllegalArgumentException("Запись задачи недоступна.");
                if(!row.isNull("dismissed_at")) throw new IllegalArgumentException("Сначала верни зачёркнутое дело в работу.");
                if(row.optJSONArray("slots")!=null && row.getJSONArray("slots").length()>0) {
                    String active=null;
                    for(int n=0;n<row.getJSONArray("slots").length();n++) {
                        String value=row.getJSONArray("slots").getString(n);
                        if(!java.time.LocalDateTime.parse(day+"T"+value).isAfter(java.time.LocalDateTime.now())) active=value;
                    }
                    if(active==null) throw new IllegalArgumentException("Первый подход ещё не наступил.");
                    mutate(data,copy(command).put("kind","check").put("time",active));return;
                }
                boolean done=command.getBoolean("completed");
                if(row.getString("status").equals(done?"done":"pending")) return;
                op.put("date",day).put("completed",done).put("completed_at",done?OffsetDateTime.now()
                    .truncatedTo(java.time.temporal.ChronoUnit.SECONDS)
                    .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx")):JSONObject.NULL)
                  .put("before",new JSONObject().put("status",row.getString("status")).put("completed_at",nullable(row,"completed_at")));
            } else throw new IllegalArgumentException("Неизвестное действие.");
        }
        queue.put(op);
    }
    /** Принять ответ без удаления команд, добавленных пока шёл сетевой запрос. */
    public static void accept(JSONObject data, JSONObject response, JSONArray sent) throws Exception {
        if(response.getInt("protocol")!=2) throw new IllegalArgumentException("Обнови Ритм на обоих устройствах.");
        String server=response.getString("server_id"), expected=data.optString("server_id");
        if(!expected.isEmpty() && !server.equals(expected)) throw new IllegalArgumentException("Ответ от другой базы ноутбука.");
        Set<String> sentIds=new HashSet<>(), accepted=new HashSet<>();
        for(int i=0;i<sent.length();i++) sentIds.add(sent.getJSONObject(i).getString("id"));
        JSONArray ack=response.getJSONArray("accepted"), conflicts=response.getJSONArray("conflicts");
        Map<String,String> rejected=new HashMap<>();
        for(int i=0;i<ack.length();i++) accepted.add(ack.getString(i));
        for(int i=0;i<conflicts.length();i++) {JSONObject item=conflicts.getJSONObject(i); rejected.put(item.getString("id"),item.getString("reason"));}
        if(!sentIds.containsAll(accepted) || !sentIds.containsAll(rejected.keySet())) throw new IllegalArgumentException("Некорректная квитанция синхронизации.");
        for(String id:sentIds) if(!accepted.contains(id) && !rejected.containsKey(id)) throw new IllegalArgumentException("Неполная квитанция синхронизации.");
        // Проверить обязательные массивы до изменения состояния.
        JSONArray tasks=response.getJSONArray("tasks"), occurrences=response.getJSONArray("occurrences");
        map(tasks,false);map(occurrences,true);
        JSONArray old=data.getJSONArray("pending"), remaining=new JSONArray(), saved=data.getJSONArray("conflicts");
        for(int i=0;i<old.length();i++) {
            JSONObject op=old.getJSONObject(i);String id=op.getString("id");
            if(rejected.containsKey(id)) saved.put(new JSONObject().put("operation",op).put("reason",rejected.get(id)));
            else if(!accepted.contains(id)) remaining.put(op);
        }
        data.put("pending",remaining).put("tasks",tasks).put("occurrences",occurrences).put("server_id",server)
            .put("last_sync",OffsetDateTime.now().toString()).put("sync_error","");
        mergeLegacyChecks(data);
    }
    /** Старые отметки подходов обычного дела становятся его единым выполнением. */
    static void mergeLegacyChecks(JSONObject data) throws Exception {
        Map<String,JSONObject> tasks=map(data.getJSONArray("tasks"),false),rows=map(data.getJSONArray("occurrences"),true);
        JSONArray conflicts=data.getJSONArray("conflicts"),keep=new JSONArray(),queue=data.getJSONArray("pending");
        for(int i=0;i<conflicts.length();i++) {
            JSONObject conflict=conflicts.getJSONObject(i),op=conflict.getJSONObject("operation");
            String id=op.optString("task_id"),date=op.optString("date");
            JSONObject task=tasks.get(id),row=rows.get(id+"|"+date);
            boolean busy=false;
            for(int n=0;n<queue.length();n++) {JSONObject queued=queue.getJSONObject(n);if(id.equals(queued.optString("task_id"))&&date.equals(queued.optString("date"))) busy=true;}
            boolean restoredSlot=false;
            if(!busy && op.optString("kind").equals("check") && conflict.optString("reason").equals("Подход не найден.") && row!=null && row.isNull("dismissed_at") && !row.getString("status").equals("cancelled")) {
                JSONArray slots=row.optJSONArray("slots");JSONObject checks=row.optJSONObject("checks");
                Object current=checks==null?JSONObject.NULL:nullable(checks,op.optString("time"));
                if(slots!=null && equal(current,nullable(op,"before"))) for(int n=0;n<slots.length();n++) if(slots.getString(n).equals(op.optString("time"))) restoredSlot=true;
            }
            if(restoredSlot) {
                queue.put(copy(op).put("id",UUID.randomUUID().toString()));
                continue;
            }
            if(!busy && op.optString("kind").equals("check") && task!=null && !task.optBoolean("checkins_enabled",false)
                && row!=null && row.optJSONArray("slots")==null && !row.getString("status").equals("cancelled") && row.isNull("dismissed_at")) {
                JSONObject converted=copy(op).put("id",UUID.randomUUID().toString()).put("kind","complete");
                boolean completed=op.getBoolean("completed");
                Object previous=nullable(op,"before");
                converted.put("before",completed?new JSONObject().put("status",row.getString("status")).put("completed_at",nullable(row,"completed_at"))
                    :new JSONObject().put("status",previous==JSONObject.NULL?"pending":"done").put("completed_at",previous));
                converted.remove("time");queue.put(converted);
            } else keep.put(conflict);
        }
        data.put("conflicts",keep);
    }
    /** Сохранить проигравшую правку до явного выбора, не заменяя данные молча. */
    public static void resolve(JSONObject data,int index,boolean keepPhone) throws Exception {
        JSONArray conflicts=data.getJSONArray("conflicts");JSONObject saved=conflicts.getJSONObject(index), op=saved.getJSONObject("operation");
        if(keepPhone) {
            String kind=op.getString("kind");
            if(kind.equals("create")) mutate(data,new JSONObject().put("kind","create").put("values",op.getJSONObject("values")));
            else {
                JSONObject command=copy(op);
                if(kind.equals("edit")) {
                    JSONObject current=fields(task(view(data,LocalDate.now().toString()),op.getString("task_id")));
                    JSONObject changes=op.getJSONObject("values");
                    for(Iterator<String> it=changes.keys();it.hasNext();) {String field=it.next();current.put(field,changes.get(field));}
                    command.put("values",current);
                }
                mutate(data,command);
            }
        }
        JSONArray rest=new JSONArray();for(int i=0;i<conflicts.length();i++) if(i!=index) rest.put(conflicts.get(i));data.put("conflicts",rest);
    }
    public static JSONArray plan(JSONObject body) throws Exception {
        if(body.length()!=1 || !body.has("tasks")) throw new IllegalArgumentException("Ожидается JSON с единственным полем tasks.");
        JSONArray tasks=body.getJSONArray("tasks"), checked=new JSONArray();
        if(tasks.length()<1 || tasks.length()>30) throw new IllegalArgumentException("В плане должно быть 1–30 задач.");
        for(int i=0;i<tasks.length();i++) checked.put(clean(tasks.getJSONObject(i),false));
        return checked;
    }
}
