"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const categories = {
  "Работа": "work", "Учёба": "study", "Здоровье": "health", "Личное": "personal",
};
const recurrence_names = {
  three_times_daily: "3 раза в день · 09:00, 15:00, 21:00", event: "До события · с учащением",
  once: "Один раз", daily: "Каждый день", weekdays: "По будням", weekly: "Раз в неделю",
  hourly: "Каждый час", every_3_hours: "Каждые 3 часа", every_5_hours: "Каждые 5 часов",
  every_8_hours: "Каждые 8 часов", every_12_hours: "Каждые 12 часов",
  every_3_days: "Раз в 3 дня", monthly: "Раз в месяц",
};
const view_names = {
  today: "Сегодня", calendar: "Календарь", progress: "Мой прогресс", settings: "Настройки",
};
let state = null;
let selected_date = null;
let current_view = "today";
let task_filter = "pending";
let category_filter = "all";
let editing_task = null;
let editor_day = null;
let review_date = null;
let review_generation = 0;
let tooltip_generation = 0;
let tooltip_timer = null;
let activity_signature = null;
let calendar_month = null;
let generation = 0;
let calendar_generation = 0;
let toast_timer = null;
let poll_timer = null;
let stopped = false;
let mutating = false;
let pending_confirmation = null;
let task_signature = "";
let notification_signature = "";
let award_filter="all", award_expanded=false;
let appearance_dirty = false;
let appearance_draft = null;
let appearance_saving = false;
let name_dirty = false;
let name_saving = false;
let editor_generation = 0;

/** Экранировать пользовательский текст перед вставкой в HTML. */
function escape_html(value) {
  const escapes = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  return String(value ?? "").replace(/[&<>"']/g, char => escapes[char]);
}

/** Вернуть SVG из локального набора значков. */
function icon(name, small = false) {
  return `<svg class="icon${small ? " small" : ""}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

/** Преобразовать дату в локальный полдень, избегая сдвига UTC. */
function as_date(value) {
  return new Date(value + "T12:00:00");
}

/** Сформировать локальную календарную дату. */
function iso_day(value) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

/** Отформатировать календарную дату на русском. */
function format_day(value, options = {day: "numeric", month: "long"}) {
  const text = as_date(value).toLocaleDateString("ru-RU", options);
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Сдвинуть календарную дату без вычислений в UTC. */
function add_days(value, count) {
  const result = as_date(value);
  result.setDate(result.getDate() + count);
  return iso_day(result);
}

/** Выбрать форму русского существительного для числа. */
function plural(value, forms) {
  const absolute = Math.abs(value) % 100;
  const last = absolute % 10;
  return absolute > 10 && absolute < 20 ? forms[2]
    : last === 1 ? forms[0] : last > 1 && last < 5 ? forms[1] : forms[2];
}

/** Выполнить авторизованный запрос к локальному приложению. */
async function api(path, method = "GET", body = undefined) {
  const options = {method, credentials: "same-origin", cache: "no-store"};
  if (body !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/api/v1" + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Не удалось выполнить действие.");
  return result;
}

/** Показать короткий результат действия без блокирующего alert. */
function toast(message, error = false) {
  clearTimeout(toast_timer);
  const target = $("#toast");
  target.textContent = message;
  target.classList.toggle("error", error);
  target.hidden = false;
  toast_timer = setTimeout(() => { target.hidden = true; }, error ? 6500 : 3500);
}

/** Загрузить актуальные записи; поздние старые ответы отбрасываются. */
async function load_state() {
  const request_generation = ++generation;
  try {
    const query = selected_date ? "?date=" + selected_date : "";
    const result = await api("/state" + query);
    if (request_generation !== generation || stopped) return;
    const previous_today = state?.today;
    if (previous_today && previous_today !== result.today && selected_date === previous_today) {
      selected_date = result.today;
      await load_state();
      return;
    }
    state = result;
    selected_date = state.selected_date;
    calendar_month ??= state.today.slice(0, 7);
    const older_backend = state.version !== "0.4.2";
    $("#connectionError").hidden = !older_backend;
    if (older_backend) $("#connectionError").textContent = "Работает другая версия Ритма. Заверши приложение через настройки или трей и запусти обновлённый Ritm.exe.";
    render();
  } catch (error) {
    if (request_generation !== generation || stopped) return;
    $("#connectionError").textContent = "Нет связи с приложением. " + error.message;
    $("#connectionError").hidden = false;
    if (!state) $("#taskList").innerHTML = `<div class="empty-state"><div class="empty-symbol">${icon("power")}</div><h3>Открой свой Ритм</h3><p>Запусти START_WINDOWS.cmd из распакованной папки. Если приложение уже работает, открой его через значок в трее.</p></div>`;
  }
}

/** Обновить все представления, не затрагивая редактируемые поля формы. */
function render() {
  const name = state.settings.display_name;
  $("#profileName").textContent = name;
  $("#profileLetter").textContent = name.slice(0, 1).toUpperCase();
  $("#navCount").textContent = state.tasks.filter(task => task.status === "pending").length;
  $("#notificationDot").hidden = !state.notifications.some(item => !item.read);
  render_today();
  render_progress();
  render_settings();
  if ($("#notificationDialog").open) render_notifications();
}

function day_details_html(value, compact = false) {
  const list = (rows, done) => rows.length ? `<ul>${rows.map(item => `<li class="${item.dismissed_at ? "is-dismissed" : ""}"><span>${escape_html(item.title)}${item.slot_time?" · "+item.slot_time:""}</span>${item.completed_at ? `<small>${done ? "Выполнено" : "Выполнено позже"}: ${escape_html(format_day(item.completed_at.slice(0,10)))} · ${escape_html(item.completed_at.slice(11,16))}</small>` : ""}</li>`).join("")}</ul>` : `<p class="muted">${done ? "Выполненных дел нет." : "Нет невыполненных дел."}</p>`;
  return `${compact ? `<strong>${escape_html(format_day(value.date, {day:"numeric",month:"long",year:"numeric"}))}</strong>` : ""}<section class="day-review-done"><h3>Выполнено за день · ${value.done.length}</h3>${list(value.done, true)}</section><section class="day-review-pending"><h3>${value.is_past ? "Не выполнено к концу дня" : "Ещё не выполнено"} · ${value.unfinished.length}</h3>${list(value.unfinished, false)}</section>`;
}

async function show_day_review(day, focus = false) {
  review_date = day;
  $("#reviewDate").value = day;
  const request = ++review_generation;
  try {
    const value = await api("/progress/day?date=" + encodeURIComponent(day));
    if (request !== review_generation) return;
    $("#dayReview").innerHTML = day_details_html(value);
    if (focus) $("#dayReview").scrollIntoView({behavior:"smooth", block:"center"});
  } catch (error) {
    if (request === review_generation) $("#dayReview").textContent = error.message;
  }
}

function hide_activity_tooltip() {
  ++tooltip_generation;
  $("#activityTooltip").hidden = true;
}

async function show_activity_tooltip(cell) {
  clearTimeout(tooltip_timer);
  const request = ++tooltip_generation;
  const tooltip = $("#activityTooltip");
  try {
    const value = await api("/progress/day?date=" + encodeURIComponent(cell.dataset.activityDay));
    if (request !== tooltip_generation || !cell.isConnected) return;
    tooltip.innerHTML = day_details_html(value, true);
    tooltip.hidden = false;
    const rect = cell.getBoundingClientRect();
    const left = Math.max(12, Math.min(rect.left - 140, window.innerWidth - tooltip.offsetWidth - 12));
    const top = rect.bottom + tooltip.offsetHeight + 12 < window.innerHeight ? rect.bottom + 8 : Math.max(12, rect.top - tooltip.offsetHeight - 8);
    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  } catch (_) { if (request === tooltip_generation) tooltip.hidden = true; }
}

$("#reviewDate").addEventListener("change", () => { if ($("#reviewDate").value) show_day_review($("#reviewDate").value); });
for (const event of ["mouseover", "focusin"]) $("#heatmap").addEventListener(event, value => {
  const cell = value.target.closest("[data-activity-day]");
  if (cell) return show_activity_tooltip(cell);
});
for (const event of ["mouseout", "focusout"]) $("#heatmap").addEventListener(event, () => { tooltip_timer = setTimeout(hide_activity_tooltip, 180); });
$("#activityTooltip").addEventListener("mouseenter", () => clearTimeout(tooltip_timer));
$("#activityTooltip").addEventListener("mouseleave", hide_activity_tooltip);
window.addEventListener?.("scroll", () => { if (!$("#activityTooltip").hidden) hide_activity_tooltip(); }, true);

/** Отрисовать календарную ленту и фактический прогресс выбранного дня. */
function render_today() {
  const tasks = state.tasks;
  const done = tasks.filter(task => task.status === "done").length;
  const remaining = tasks.filter(task => task.status === "pending" && !task.dismissed_at).length;
  const is_future = selected_date > state.today;
  const is_past = selected_date < state.today;
  $("#todayLabel").textContent = format_day(selected_date, {weekday: "long", day: "numeric", month: "long"}).toUpperCase();
  $("#weekRange").textContent = `${format_day(state.week[0].date)} — ${format_day(state.week[6].date)}`;
  $("#weekStrip").innerHTML = state.week.map(day => `<button class="day-tile${day.date === selected_date ? " active" : ""}${day.date === state.today ? " is-today" : ""}" data-date="${day.date}" aria-pressed="${day.date === selected_date}" aria-label="${escape_html(format_day(day.date))}: задач ${day.total}, выполнено ${day.done}"><span class="weekday">${format_day(day.date, {weekday: "short"})}</span><span class="day-number">${as_date(day.date).getDate()}</span>${day.total ? '<i class="day-dot"></i>' : ""}</button>`).join("");
  $("#listTitle").innerHTML = `План на день <span>${tasks.length}</span>`;
  $("#quickTitle").disabled = is_past;
  $("#quickTitle").placeholder = is_past ? "Этот день уже в истории" : "Что сделаем сегодня?";
  $("#quickDetails").disabled = is_past;
  $("#quickAdd button[type=submit]").disabled = is_past;
  $("#newTask").disabled = is_past;
  $("#categoryFilters").innerHTML = `<button class="category-chip${category_filter === "all" ? " selected" : ""}" data-category-filter="all" aria-pressed="${category_filter === "all"}">Все категории</button>` + Object.entries(categories).map(([name, symbol]) => `<button class="category-chip${category_filter === name ? " selected" : ""}" data-category-filter="${name}" aria-pressed="${category_filter === name}">${icon(symbol)}${name}</button>`).join("");
  $$("[data-filter]").forEach(button => {
    button.classList.toggle("selected", button.dataset.filter === task_filter);
    button.setAttribute("aria-pressed", String(button.dataset.filter === task_filter));
  });
  const signature = JSON.stringify([tasks, task_filter, category_filter, selected_date]);
  if (signature !== task_signature) {
    const visible = tasks.filter(task => (task_filter === "all" || task.status === task_filter) && (category_filter === "all" || category_filter === task.category));
    $("#taskList").innerHTML = visible.length
      ? visible.map(task => render_task(task, is_future)).join("") : render_empty(tasks.length);
    task_signature = signature;
  }
  $("#completedLink").innerHTML = done && task_filter === "pending" ? `<button class="completed-shortcut" data-route="progress">${icon("check")}<span>Готово за этот день: ${done} · посмотреть прогресс</span>${icon("arrow")}</button>` : "";
  const percent = tasks.length ? Math.round(done / tasks.length * 100) : 0;
  const circumference = 2 * Math.PI * 66;
  const message = !tasks.length ? "Всё начинается с первой задачи"
    : percent === 100 ? "Отличный ритм. Ты справился!"
    : done ? "Одно выполненное дело — уже шаг" : "Выбери один маленький шаг";
  $("#dayProgress").innerHTML = `<section class="day-card"><p class="eyebrow">${is_future ? "ПЛАН НА БУДУЩЕЕ" : "ФОКУС ДНЯ"}</p><h3>Шаг за шагом</h3><div class="ring-wrap" role="img" aria-label="Выполнено ${done} из ${tasks.length} задач"><svg class="progress-ring" viewBox="0 0 175 175" aria-hidden="true"><circle class="ring-track" cx="87.5" cy="87.5" r="66"/><circle class="ring-glow" cx="87.5" cy="87.5" r="66" stroke-dasharray="${circumference}" stroke-dashoffset="${circumference * (1 - percent / 100)}"${!done ? ' opacity="0"' : ""}/></svg><div class="ring-center"><strong>${percent}<span>%</span></strong><small>Твой прогресс дня</small></div></div><p class="day-message">${message}</p><div class="day-card-stats"><div><strong>${String(done).padStart(2, "0")}</strong><span>Завершено</span></div><div><strong>${String(remaining).padStart(2, "0")}</strong><span>Впереди</span></div></div></section>`;
  const week_done = state.week.reduce((sum, item) => sum + item.done, 0);
  const maximum = Math.max(4, ...state.week.map(item => item.done));
  $("#weeklyProgress").innerHTML = `<section class="weekly-card"><div class="panel-mini-heading"><h3>Ритм недели</h3><span>7 ДНЕЙ</span></div><div class="mini-chart" role="img" aria-label="Выполнено по дням: ${state.week.map(day => `${format_day(day.date, {weekday: "short"})}: ${day.done}`).join(", ")}">${state.week.map(day => `<div class="chart-item${day.date === state.today ? " is-today" : ""}" title="${escape_html(format_day(day.date))}: выполнено ${day.done}"><div class="chart-bar${day.done ? " has-data" : ""}" style="height:${day.done ? Math.max(12, day.done / maximum * 78) : 3}px"></div><span>${format_day(day.date, {weekday: "short"})}</span></div>`).join("")}</div><p class="weekly-total"><strong>${week_done}</strong>Выполнено за эту неделю</p></section>`;
}

/** Показать одну задачу с отдельными действиями выполнения и редактирования. */
function render_task(task, is_future) {
  const done = task.status === "done";
  const dismissed = Boolean(task.dismissed_at);
  const interval = !!task.checkins?.length;
  const dismiss_action = !done && (dismissed || task.can_dismiss) ? `<button class="button subtle dismiss-task" data-dismiss="${task.id}" data-dismissed="${!dismissed}">${dismissed ? "Вернуть" : "Зачеркнуть"}</button>` : "";
  const grace_label = task.in_grace ? `<span class="grace-label">Продлено до ${new Date(task.grace_until).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"})}</span>` : dismissed ? '<span class="grace-label">Зачёркнуто · −5 XP</span>' : "";
  const reminder_end = task.reminder_end_date || (task.recurrence === "once" ? task.date : null);
  const reminder_expired = (reminder_end && reminder_end < state.today) || (task.recurrence === "once" && task.start_date && task.date !== task.start_date);
  const reminder_active = task.reminder && !reminder_expired;
  const hourly = /^(hourly|every_\d+_hours)$/.test(task.recurrence);
  const overdue = !hourly && !done && task.time && task.date <= state.today && `${task.date}T${task.time}` < state.server_time.slice(0, 16);
  const label = `${done ? "Отменить выполнение" : "Выполнить"}: ${task.title}`;
  return `<article class="task-card${done ? " is-done" : ""}${dismissed ? " is-dismissed" : ""}" data-category="${escape_html(task.category)}"><button class="task-check" data-complete="${task.id}" data-completed="${!done}" aria-label="${escape_html(label)}" aria-pressed="${done}"${is_future || dismissed || interval ? " disabled" : ""}>${done ? icon("check") : ""}</button><span class="category-icon">${icon(categories[task.category] || "personal")}</span><div class="task-content"><h3 class="task-title">${!task.archived_date ? `<button class="task-title-edit" data-edit="${task.task_id}" aria-label="Открыть задачу: ${escape_html(task.title)}">${escape_html(task.title)}</button>` : escape_html(task.title)}</h3><div class="task-meta">${grace_label}<span class="category-text">${escape_html(task.category)}</span><i class="dot"></i><span class="recurrence">${task.recurrence !== "once" ? icon("repeat") : ""}${recurrence_names[task.recurrence] || ""}</span>${task.date < selected_date ? `<i class="dot"></i><span>С ${format_day(task.date)}</span>` : ""}${task.snooze_until ? `<i class="dot"></i><span>Отложено до ${new Date(task.snooze_until).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"})}</span>` : ""}</div>${window.RitmProgress.checks(task)}${task.notes ? `<p class="notes-preview">${escape_html(task.notes)}</p>` : ""}</div><div class="task-end">${dismiss_action}${task.time ? `<span class="task-time${overdue ? " overdue" : ""}" title="${reminder_expired ? "Срок напоминания завершён" : reminder_active ? "Напоминание включено" : "Время задачи, без уведомления"}">${icon(reminder_active ? "bell" : "clock")}<span>${hourly ? "С " : ""}${task.time}</span></span>` : ""}<i class="priority-dot ${task.priority}" title="${task.priority === "high" ? "Важное" : task.priority === "low" ? "Без спешки" : "Обычный приоритет"}"></i>${!task.archived_date ? `<button class="button subtle task-edit" data-edit="${task.task_id}" aria-label="Редактировать: ${escape_html(task.title)}">${icon("edit")}<span>Изменить</span></button>` : ""}</div></article>`;
}

/** Сформировать пустое состояние с действиями по текущему контексту. */
function render_empty(total) {
  let heading = "Всё начинается с одного дела";
  let text = "Запиши то, что важно сегодня. Одна задача — уже хороший план.";
  let symbol = "plus";
  let actions = selected_date >= state.today ? `<button class="button subtle" data-add>${icon("plus", true)}Добавить задачу</button>` : "";
  if (total && task_filter === "done") {
    heading = "Победы ещё впереди"; text = "Отметь выполненную задачу — и она появится здесь и в твоём прогрессе."; symbol = "spark"; actions = "";
  } else if (total && category_filter !== "all") {
    heading = "Здесь пока тихо"; text = "В этой категории нет задач для выбранного фильтра."; symbol = "sun";
  } else if (total && task_filter === "pending") {
    heading = "На сегодня всё. Ты справился!"; text = "Все задачи этого дня выполнены. Самое время заметить свой прогресс."; symbol = "check"; actions = `<button class="button subtle" data-route="progress">Мои победы${icon("arrow", true)}</button>`;
  } else if (selected_date < state.today) {
    heading = "Этот день — чистый лист"; text = "Здесь нет сохранённых задач. Будущие маленькие шаги появятся в истории."; symbol = "calendar";
  } else if (selected_date > state.today) {
    heading = "Место для хорошего плана"; text = "Добавь задачу на этот день или создай повторяющуюся привычку."; symbol = "calendar";
  }
  if (!state.has_tasks && selected_date === state.today) actions += '<button class="text-button" data-demo>Попробовать на примерах</button>';
  return `<div class="empty-state"><div class="empty-symbol">${icon(symbol)}</div><h3>${heading}</h3><p>${text}</p><div class="empty-actions">${actions}</div></div>`;
}

/** Показать накопленный прогресс без выдуманных счётчиков. */
function render_progress() {
  const summary = state.summary;
  const stats = [
    ["Сделано тобой", summary.done, "check", "#64d9b5", "За всё время"],
    ["Серия", summary.streak, "flame", "#f0bb81", "Дней с выполненными задачами"],
    ["Лучшая серия", summary.longest_streak, "bolt", "#79bfff", "Дней подряд"],
    ["Твой уровень", String(summary.level).padStart(2, "0"), "spark", "#b1a3ff", `${summary.xp} XP · +10 за выполнение, −5 за зачёркивание`],
  ];
  $("#progressStats").innerHTML = stats.map(([title, value, symbol, color, caption], index) => `<div class="panel progress-stat" style="--stat-color:${color}"><div class="stat-top"><span>${title}</span>${icon(symbol)}</div><p class="stat-number">${value}</p><p class="stat-caption">${caption}</p>${index === 3 ? `<div class="level-track"><div style="width:${Math.max(0,summary.xp) % 100}%"></div></div>` : ""}</div>`).join("");
  const cells = [...state.activity];
  const start_weekday = (as_date(cells[0].date).getDay() + 6) % 7;
  const padding = Array.from({length: start_weekday}, () => '<span aria-hidden="true"></span>').join("");
  const activity_value = JSON.stringify(cells);
  if (activity_signature !== activity_value) {
    $("#heatmap").innerHTML = padding + cells.map(day => `<button class="heatmap-cell" data-activity-day="${day.date}" data-intensity="${day.done >= 5 ? 3 : day.done >= 3 ? 2 : day.done ? 1 : 0}" aria-label="${escape_html(format_day(day.date))}: выполнено ${day.done}, не выполнено ${day.unfinished || 0}" aria-describedby="activityTooltip"></button>`).join("");
    activity_signature = activity_value;
  }
  $("#unfinishedCount").textContent = String((state.unfinished || []).length);
  $("#unfinishedList").innerHTML = (state.unfinished || []).length
    ? state.unfinished.map(task => `<div class="unfinished-entry"><p class="history-date">План на ${format_day(task.date, {day:"numeric",month:"long",year:"numeric"})}</p>${render_task(task, false)}</div>`).join("")
    : '<p class="history-empty">Пропущенных дел пока нет.</p>';
  $("#reviewDate").max = state.today;
  show_day_review(review_date || state.today);
  $("#historyCount").textContent = summary.done ? (summary.done > 100 ? "Последние 100" : `${summary.done} ${plural(summary.done, ["задача", "задачи", "задач"])}`) : "";
  let previous = "";
  $("#historyList").innerHTML = state.history.length ? state.history.map(item => {
    const day = item.completed_at.slice(0, 10);
    const heading = previous !== day ? `<p class="history-date">${format_day(day, {day: "numeric", month: "long", year: "numeric"})}</p>` : "";
    previous = day;
    const clock = new Date(item.completed_at).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
    return `${heading}<div class="history-row" data-category="${escape_html(item.category)}"><span class="history-check">${icon("check")}</span><div class="history-info"><strong>${escape_html(item.title)}${item.slot_time?" · "+item.slot_time:""}</strong><small>${escape_html(item.category)} · +10 XP</small></div><time>${clock}</time><button class="text-button" ${item.slot_time ? `data-check-slot="${item.slot_time}" data-slot-id="${item.id}" data-slot-completed="false"` : `data-complete="${item.id}" data-completed="false"`} aria-label="Отменить выполнение: ${escape_html(item.title)}">Отменить</button></div>`;
  }).join("") : '<p class="history-empty">Первая победа уже рядом.<br>Отметь задачу выполненной — история начнётся.</p>';
  $("#achievements").innerHTML = window.RitmProgress.html(window.RitmProgress.awards(state.progress_events||state.history,state.tasks,state.today,state.progress_facts),award_filter,award_expanded);
}

/** Обновить настройки и показывать реальную доступность Windows tray. */
function render_settings() {
  render_appearance();
  if (!name_dirty && !name_saving && document.activeElement !== $("#displayName")) $("#displayName").value = state.settings.display_name;
  $("#notificationsToggle").checked = state.settings.notifications;
  $("#soundToggle").checked = state.settings.sound;
  $("#autostartToggle").checked = state.settings.autostart;
  $("#autostartToggle").disabled = !state.runtime.autostart_supported;
  $("#nativeStatus").classList.toggle("warning", !state.runtime.notifications_available || !!state.runtime.notification_error);
  $("#nativeStatus").textContent = state.runtime.notification_error || (state.runtime.notifications_available
    ? "Ритм отправляет системные уведомления Windows. Они сохраняются в центре уведомлений; режим «Не беспокоить» может скрывать баннеры. Во время сна и выключения ПК доставка недоступна."
    : "Системные уведомления недоступны в этом запуске. Напоминания сохраняются в журнале приложения.");
  $("#dataPath").textContent = state.runtime.data_directory;
  $("#appVersion").textContent = state.version;

}

/** Синхронизировать сохранённую тему, не затирая открытый предпросмотр. */
function render_appearance() {
  if (appearance_dirty) return;
  appearance_draft = window.RitmTheme.apply(state.settings);
  sync_appearance_controls();
}

/** Заполнить все видимые образцы цветами текущей гаммы. */
function sync_appearance_controls() {
  if (!appearance_draft) return;
  const resolved = window.RitmTheme.resolve(appearance_draft);
  $$('#appearanceForm input[name="theme"]').forEach(input => {
    input.checked = input.value === appearance_draft.theme;
  });
  $$('[data-appearance-range]').forEach(input => { input.value = appearance_draft[input.dataset.appearanceRange]; });
  $$('[data-appearance-bool]').forEach(input => { input.checked = appearance_draft[input.dataset.appearanceBool]; });
  $$('[data-appearance-color]').forEach(input => {
    const key = input.dataset.appearanceColor;
    input.value = key === "accent_color" ? appearance_draft[key] : resolved.palette[window.RitmTheme.colors[key]] || appearance_draft.accent_color;
  });
  $$('[data-auto-color]').forEach(button => {
    const key = button.dataset.autoColor;
    button.disabled = key !== "accent_color" && !appearance_draft[key];
    button.setAttribute("aria-label", "Автоматический цвет: " + $("label", button.parentElement).textContent);
  });
  for (const [id, key, unit] of [
    ["paletteHueValue", "palette_hue", "°"], ["paletteSaturationValue", "palette_saturation", "%"],
    ["backgroundBrightnessValue", "background_brightness", "%"], ["neonValue", "neon_intensity", "%"],
    ["borderRadiusValue", "border_radius", " px"],
  ]) $("#" + id).textContent = appearance_draft[key] + unit;
  $("#neonIntensity").disabled = !appearance_draft.glow_enabled;
  $("#contrastWarning").hidden = !resolved.low_contrast;
  $("#appearanceSave").disabled = !appearance_dirty || appearance_saving;
  $("#appearanceCancel").hidden = !appearance_dirty;
  $("#appearanceStatus").textContent = appearance_saving ? "Сохраняем оформление…" : appearance_dirty
    ? "Предпросмотр. Нажми «Сохранить тему», чтобы оставить изменения."
    : "Оформление сохранено. Применяется ко всем страницам.";
}

/** Применить черновик, не сохраняя его до явного нажатия кнопки. */
function preview_appearance(changes = {}) {
  if (!state) return;
  appearance_draft = window.RitmTheme.normalize({...appearance_draft, ...changes});
  appearance_dirty = JSON.stringify(appearance_draft) !== JSON.stringify(window.RitmTheme.normalize(state.settings));
  window.RitmTheme.apply(appearance_draft, {cache: false});
  sync_appearance_controls();
}

/** Обновить гамму сразу для всех поверхностей и сбросить частные цвета. */
function palette_change(changes = {}) {
  const draft = {...appearance_draft, ...changes};
  const overrides = Object.fromEntries(Object.keys(window.RitmTheme.colors).map(key => [key, null]));
  preview_appearance({...overrides, ...changes, palette_enabled: true,
    accent_color: window.RitmTheme.hsl(draft.palette_hue, draft.palette_saturation, 60)});
}

/** Загрузить и отрисовать сетку выбранного месяца. */
async function render_calendar() {
  if (!state) return;
  const request_generation = ++calendar_generation;
  $("#calendarMonth").textContent = format_day(calendar_month + "-01", {month: "long", year: "numeric"});
  try {
    const result = await api("/calendar?month=" + calendar_month);
    if (request_generation !== calendar_generation) return;
    $("#calendarGrid").innerHTML = result.days.map(day => `<button class="calendar-day${day.date.slice(0, 7) !== calendar_month ? " outside" : ""}${day.date === state.today ? " today" : ""}" data-date="${day.date}" aria-label="${escape_html(format_day(day.date))}: ${day.tasks.length} задач"><span>${as_date(day.date).getDate()}</span>${day.tasks.slice(0, 2).map(task => `<div class="calendar-event${task.status === "done" ? " done" : ""}${task.dismissed_at ? " is-dismissed" : ""}" data-category="${escape_html(task.category)}">${task.time ? task.time + " " : ""}${escape_html(task.title)}</div>`).join("")}${day.tasks.length > 2 ? `<small class="calendar-more">Ещё ${day.tasks.length - 2}</small>` : ""}</button>`).join("");
  } catch (error) { toast(error.message, true); }
}

/** Переключить страницу, сохраняя выбранную дату при переходе из календаря. */
function change_view(view, keep_date = false) {
  if (!(view in view_names)) return;
  current_view = view;
  $$(".view").forEach(section => { section.hidden = section.id !== view + "View"; });
  $$(".nav-button[data-view]").forEach(button => {
    button.classList.toggle("active", button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
    button.title = view_names[button.dataset.view];
  });
  $("#breadcrumb").textContent = view_names[view];
  document.title = "Ритм · " + view_names[view];
  if (view === "today" && !keep_date && state) {
    selected_date = state.today;
    load_state();
  }
  if (view === "calendar") render_calendar();
  window.scrollTo({top: 0, behavior: "instant"});
}

/** Открыть создание задачи или редактор её активного расписания. */
async function open_task(task_id = null) {
  if (!state) return toast("Подожди подключения к приложению.");
  if (mutating) return;
  if (!task_id && selected_date < state.today) return toast("Создавай новые задачи на сегодня или будущий день.");
  const request_generation = ++editor_generation;
  let task = null;
  try {
    if (task_id) {
      const result = await api("/tasks");
      if (request_generation !== editor_generation) return;
      task = result.tasks.find(task => task.id === task_id);
      if (!task) throw new Error("Это расписание уже в архиве.");
    }
    editing_task = task;
    $("#taskForm").reset();
    $("#taskTitle").value = task ? task.title : $("#quickTitle").value.trim();
    $("#taskCategory").value = task?.category || (category_filter === "all" ? "Личное" : category_filter);
    $("#taskPriority").value = task?.priority || "normal";
    $("#taskDate").min = task && task.start_date < state.today ? task.start_date : state.today;
    $("#taskDate").value = task?.start_date || selected_date;
    $("#taskRecurrence").value = task?.recurrence || "once";
    $("#taskTime").value = task ? (task.time || "") : "09:00";
    $("#taskRepeatUntil").value=task?.repeat_until || "21:00";
    $("#taskCheckins").checked=Boolean(task?.checkins_enabled);
    $("#taskReminderEnd").value = task ? (task.reminder_end_date || (task.recurrence === "once" ? task.start_date : "")) : $("#taskDate").value;
    editor_day = $("#taskDate").value;
    $("#taskEventDate").value = task?.event_date || "";
    $("#taskEscalationDays").value = task?.escalation_days ?? 3;
    $("#taskReminder").checked = task?.reminder || false;
    update_reminder_fields();
    $("#taskNotes").value = task?.notes || "";
    $("#taskDialogTitle").textContent = task ? "Редактировать задачу" : "Новая задача";
    $("#saveTask").innerHTML = (task ? "Сохранить" : "Добавить задачу") + icon("arrow", true);
    $("#archiveTask").hidden = !task;
    $("#editHint").hidden = !task;
    $("#taskError").hidden = true;
    $("#taskDialog").showModal();
    $("#taskTitle").focus();
  } catch (error) { toast(error.message, true); }
}

/** Выполнить или отменить задачу с сохранением единственного выполнения. */
async function complete_task(id, completed, button) {
  if (mutating) return;
  const previous_xp = state?.summary.xp || 0;
  mutating = true;
  if (button) button.disabled = true;
  try {
    await api(`/occurrences/${id}/complete`, "POST", {completed});
    await load_state();
    if (current_view === "calendar") await render_calendar();
    const gained = (state?.summary.xp || 0) - previous_xp;
    toast(completed ? (gained > 0 ? `Ещё один маленький шаг. +${gained} XP ✦` : "Задача отмечена выполненной") : "Отметка отменена. Можно вернуться к задаче.");
  } catch (error) { toast(error.message, true); }
  finally { mutating = false; if (button) button.disabled = false; }
}

/** Открыть конкретный день из календаря или карты активности. */
function select_day(value) {
  selected_date = value;
  if (current_view !== "today") change_view("today", true);
  load_state();
}

/** Показать подтверждение архивирования или полной остановки. */
function confirm_action(title, text, callback) {
  $("#confirmTitle").textContent = title;
  $("#confirmText").textContent = text;
  pending_confirmation = callback;
  $("#confirmDialog").showModal();
}

/** Показать локальный журнал доставленных и отложенных событий. */
function render_notifications() {
  if (!state) return;
  const signature = JSON.stringify(state.notifications);
  if (signature === notification_signature) return;
  notification_signature = signature;
  $("#notificationList").innerHTML = state.notifications.length ? state.notifications.map(item => `<article class="notification-item${!item.read ? " unread" : ""}"><time>${new Date(item.created_at).toLocaleString("ru-RU", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})}</time><h3>${escape_html(item.title)}</h3><p>${escape_html(item.body)}</p>${item.occurrence_id && item.task_status === "pending" ? `<div class="notification-item-actions"><button data-complete="${item.occurrence_id}" data-completed="true">${icon("check", true)}Выполнено</button><button data-snooze="${item.occurrence_id}">Отложить на 10 мин</button></div>` : ""}</article>`).join("") : '<div class="empty-state"><div class="empty-symbol">' + icon("bell") + '</div><h3>Здесь пока тихо</h3><p>Включи напоминание в задаче — и Ритм позовёт в нужное время.</p></div>';
  $("#readAllNotifications").hidden = !state.notifications.some(item => !item.read);
}

document.addEventListener("click", async event => {
  const button = event.target.closest("button, a");
  if (!button || button.disabled) return;
  if (button.dataset.view) return change_view(button.dataset.view);
  if (button.dataset.route) return change_view(button.dataset.route);
  if (button.dataset.awardFilter) {award_filter=button.dataset.awardFilter;award_expanded=false;return render_progress();}
  if (button.dataset.awardExpand) {award_expanded=button.dataset.awardExpand==='true';return render_progress();}
  if (button.dataset.checkSlot) {
    try {button.disabled=true;await api(`/occurrences/${button.dataset.slotId}/check`,"POST",{time:button.dataset.checkSlot,completed:button.dataset.slotCompleted==='true'});await load_state();}
    catch(error){toast(error.message,true);}finally{button.disabled=false;}return;
  }
  if (button.dataset.activityDay) { hide_activity_tooltip(); return show_day_review(button.dataset.activityDay, true); }
  if (button.dataset.date) return select_day(button.dataset.date);
  if (button.hasAttribute("data-add")) return open_task();
  if (button.dataset.close) return $("#" + button.dataset.close).close();
  if (button.dataset.filter) { task_filter = button.dataset.filter; return state && render_today(); }
  if (button.dataset.categoryFilter) { category_filter = button.dataset.categoryFilter; return state && render_today(); }
  if (button.dataset.edit) return open_task(button.dataset.edit);
  if (button.dataset.complete) return complete_task(Number(button.dataset.complete), button.dataset.completed === "true", button);
  if (button.dataset.dismiss) {
    try {
      button.disabled = true;
      await api(`/occurrences/${button.dataset.dismiss}/dismiss`, "POST", {dismissed:button.dataset.dismissed === "true"});
      await load_state();
      toast(button.dataset.dismissed === "true" ? "Дело зачёркнуто. −5 XP" : "Дело возвращено. +5 XP");
    } catch (error) { toast(error.message,true); }
    finally { button.disabled = false; }
  }
  if (button.dataset.snooze) {
    try {
      button.disabled = true;
      await api(`/occurrences/${button.dataset.snooze}/snooze`, "POST", {minutes: 10});
      await load_state();
      toast("Напомню через 10 минут");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }
  if (button.hasAttribute("data-demo")) {
    try {
      button.disabled = true;
      await api("/demo", "POST", {});
      await load_state();
      toast("Примеры добавлены. Их можно изменить или убрать в архив.");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  }
});

$("#newTask").addEventListener("click", () => open_task());
$("#quickDetails").addEventListener("click", () => open_task());
$("#calendarAdd").addEventListener("click", () => { if (!state) return; selected_date = state.today; open_task(); });
$("#goToday").addEventListener("click", () => state && select_day(state.today));
$("#prevWeek").addEventListener("click", () => state && select_day(add_days(selected_date, -7)));
$("#nextWeek").addEventListener("click", () => state && select_day(add_days(selected_date, 7)));

$("#quickAdd").addEventListener("submit", async event => {
  event.preventDefault();
  if (mutating || !state) return;
  const title = $("#quickTitle").value.trim();
  if (!title) return $("#quickTitle").focus();
  mutating = true;
  const button = $("#quickAdd button[type=submit]");
  button.disabled = true;
  try {
    await api("/tasks", "POST", {title, start_date: selected_date, category: category_filter === "all" ? "Личное" : category_filter});
    $("#quickTitle").value = "";
    task_filter = "pending";
    await load_state();
    toast("Задача добавлена. Первый шаг — уже сделан.");
  } catch (error) { toast(error.message, true); }
  finally { mutating = false; button.disabled = false; }
});

$("#taskReminder").addEventListener("change", () => {
  $("#taskTime").required = $("#taskReminder").checked;
  if ($("#taskReminder").checked && !$("#taskTime").value) $("#taskTime").value = "09:00";
});

$("#taskForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (mutating) return;
  mutating = true;
  $("#saveTask").disabled = true;
  $("#taskError").hidden = true;
  const body = {
    title: $("#taskTitle").value, category: $("#taskCategory").value,
    priority: $("#taskPriority").value, start_date: $("#taskDate").value,
    recurrence: $("#taskRecurrence").value, reminder: $("#taskReminder").checked,
    reminder_end_date: $("#taskReminderEnd").value || null,
    event_date: $("#taskRecurrence").value === "event" ? ($("#taskEventDate").value || null) : null,
    escalation_days: Number($("#taskEscalationDays").value),
    repeat_until: $("#taskCheckins").checked ? ($("#taskRepeatUntil").value || null) : null,
    checkins_enabled: $("#taskCheckins").checked,
    time: $("#taskTime").value || null, notes: $("#taskNotes").value,
  };
  try {
    await api(editing_task ? `/tasks/${editing_task.id}` : "/tasks", editing_task ? "PATCH" : "POST", body);
    $("#taskDialog").close();
    $("#quickTitle").value = "";
    task_filter = "pending";
    await load_state();
    if (current_view === "calendar") await render_calendar();
    toast(editing_task ? "План обновлён. История на месте." : "Задача добавлена");
  } catch (error) {
    $("#taskError").textContent = error.message;
    $("#taskError").hidden = false;
  } finally { mutating = false; $("#saveTask").disabled = false; }
});

$("#archiveTask").addEventListener("click", () => {
  if (!editing_task) return;
  const task = editing_task;
  confirm_action("Убрать задачу в архив?", "Новые повторения перестанут появляться. Все прошлые выполнения и прогресс сохранятся.", async () => {
    await api(`/tasks/${task.id}`, "DELETE", {});
    $("#taskDialog").close();
    await load_state();
    if (current_view === "calendar") await render_calendar();
    toast("Задача в архиве. История сохранена.");
  });
});

$("#confirmAction").addEventListener("click", async () => {
  if (!pending_confirmation) return;
  $("#confirmAction").disabled = true;
  try { await pending_confirmation(); $("#confirmDialog").close(); }
  catch (error) { toast(error.message, true); }
  finally { $("#confirmAction").disabled = false; pending_confirmation = null; }
});

$("#notificationsButton").addEventListener("click", () => {
  if (!state) return;
  notification_signature = "";
  render_notifications();
  $("#notificationDialog").showModal();
});
$("#readAllNotifications").addEventListener("click", async () => {
  try {
    await api("/notifications/read", "POST", {ids: state.notifications.filter(item => !item.read).map(item => item.id)});
    await load_state();
  } catch (error) { toast(error.message, true); }
});

$("#appearanceForm").addEventListener("input", event => {
  const input = event.target;
  if (input.name === "theme") {
    const presets = {neon: [190, 80, 6, "#35dcf7"], midnight: [255, 45, 7, "#a08bff"], light: [215, 65, 95, "#3478d4"]};
    const [h, s, l, accent] = presets[input.value];
    const colors = Object.fromEntries(Object.keys(window.RitmTheme.colors).map(key => [key, null]));
    return preview_appearance({...colors, theme: input.value, palette_enabled: false,
      palette_hue: h, palette_saturation: s, background_brightness: l, accent_color: accent});
  }
  const key = input.dataset.appearanceRange;
  if (key) {
    const changes = {[key]: Number(input.value)};
    return ["palette_hue", "palette_saturation", "background_brightness"].includes(key)
      ? palette_change(changes) : preview_appearance(changes);
  }
  if (input.dataset.appearanceColor) preview_appearance({[input.dataset.appearanceColor]: input.value});
  if (input.dataset.appearanceBool) preview_appearance({[input.dataset.appearanceBool]: input.checked});
});
$$('[data-palette]').forEach(button => button.addEventListener("click", () => {
  const presets = {ocean: [195, 85], violet: [265, 80], forest: [150, 65], sunset: [25, 90]};
  const [hue, saturation] = presets[button.dataset.palette];
  palette_change({palette_hue: hue, palette_saturation: saturation});
}));
$$('[data-auto-color]').forEach(button => button.addEventListener("click", () => {
  const key = button.dataset.autoColor;
  preview_appearance({[key]: key === "accent_color"
    ? window.RitmTheme.hsl(appearance_draft.palette_hue, appearance_draft.palette_saturation, 60) : null});
}));
$("#appearanceForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state || appearance_saving || !appearance_dirty || !$("#appearanceForm").reportValidity()) return;
  const submitted = {...appearance_draft};
  appearance_saving = true;
  sync_appearance_controls();
  try {
    const saved = await api("/settings", "PATCH", submitted);
    generation++;
    state.settings = saved;
    if (JSON.stringify(appearance_draft) === JSON.stringify(submitted)) {
      appearance_dirty = false;
      render_appearance();
    } else {
      appearance_dirty = JSON.stringify(appearance_draft) !== JSON.stringify(window.RitmTheme.normalize(saved));
      window.RitmTheme.apply(saved);
      window.RitmTheme.apply(appearance_draft, {cache: false});
      sync_appearance_controls();
    }
    toast("Оформление сохранено");
  } catch (error) {
    toast(error.message, true);
  } finally { appearance_saving = false; sync_appearance_controls(); }
});
$("#appearanceCancel").addEventListener("click", () => {
  appearance_dirty = false;
  if (state) render_appearance();
});
$("#appearanceReset").addEventListener("click", () => {
  if (!state) return;
  appearance_draft = {...window.RitmTheme.defaults};
  appearance_dirty = JSON.stringify(appearance_draft) !== JSON.stringify(window.RitmTheme.normalize(state.settings));
  window.RitmTheme.apply(appearance_draft, {cache: false});
  sync_appearance_controls();
});
$("#displayName").addEventListener("input", () => { name_dirty = true; });
$("#nameForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state || name_saving) return;
  const submitted = $("#displayName").value;
  name_saving = true;
  const button = $('#nameForm button[type="submit"]');
  button.disabled = true;
  try {
    const saved = await api("/settings", "PATCH", {display_name: submitted});
    generation++;
    state.settings.display_name = saved.display_name;
    if ($("#displayName").value === submitted) {
      name_dirty = false;
      $("#displayName").value = saved.display_name;
    }
    render();
    toast("Имя сохранено");
  } catch (error) { toast(error.message, true); }
  finally { name_saving = false; button.disabled = false; }
});
for (const [selector, key] of [["#notificationsToggle", "notifications"], ["#soundToggle", "sound"], ["#autostartToggle", "autostart"]]) {
  $(selector).addEventListener("change", async event => {
    const input = event.target;
    input.disabled = true;
    try { await api("/settings", "PATCH", {[key]: input.checked}); await load_state(); toast("Настройка сохранена"); }
    catch (error) { input.checked = !input.checked; toast(error.message, true); }
    finally { input.disabled = key === "autostart" && !state?.runtime?.autostart_supported; }
  });
}
$("#testNotification").addEventListener("click", async () => {
  const button = $("#testNotification");
  button.disabled = true;
  try {
    const result = await api("/notification-test", "POST", {});
    await load_state();
    toast(result.delivery?.message || "Проверка добавлена в журнал напоминаний; подтверждение Windows не получено.", !result.delivery?.accepted);
  }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$("#quitApp").addEventListener("click", () => confirm_action("Завершить Ритм?", "Фоновый процесс и напоминания остановятся до следующего запуска. Все задачи и прогресс останутся сохранены.", async () => {
  await api("/app/quit", "POST", {});
  stopped = true;
  clearInterval(poll_timer);
  const overlay = document.createElement("div");
  overlay.className = "stopped";
  overlay.innerHTML = '<img src="/icon.png" alt=""><h2>Увидимся в твоём ритме.</h2><p>Данные сохранены. Для нового запуска открой START_WINDOWS.cmd.</p>';
  document.body.append(overlay);
}));

/** Переключить месяц и сохранить календарный формат. */
function shift_month(delta) {
  if (!state) return;
  const current = as_date(calendar_month + "-01");
  current.setMonth(current.getMonth() + delta);
  calendar_month = iso_day(current).slice(0, 7);
  render_calendar();
}
$("#prevMonth").addEventListener("click", () => shift_month(-1));
$("#nextMonth").addEventListener("click", () => shift_month(1));
$("#calendarToday").addEventListener("click", () => { if (state) { calendar_month = state.today.slice(0, 7); render_calendar(); } });
let escape_closes_dialog = false;
let fullscreen_exit_pending = false;
document.addEventListener("keydown", event => {
  if (event.key === "Escape") hide_activity_tooltip();
  if (event.key === "Escape") {
    if (!event.repeat) escape_closes_dialog = $$("dialog").some(dialog => dialog.open);
    return;
  }
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  if (event.key.toLowerCase() === "n" && !typing && !event.ctrlKey && !event.altKey && !event.metaKey && !$$("dialog").some(dialog => dialog.open)) {
    event.preventDefault(); open_task();
  }
}, true);
document.addEventListener("keyup", async event => {
  if (event.key !== "Escape" || event.repeat) return;
  const dismissed_dialog = escape_closes_dialog || $$("dialog").some(dialog => dialog.open);
  escape_closes_dialog = false;
  if (dismissed_dialog || fullscreen_exit_pending) return;
  fullscreen_exit_pending = true;
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if (state?.runtime?.platform === "nt") await api("/app/exit-fullscreen", "POST", {});
  } catch (error) {
    toast("Для выхода из полноэкранного режима нажми F11.", true);
  } finally {
    fullscreen_exit_pending = false;
  }
}, true);
document.addEventListener("visibilitychange", () => { if (!document.hidden && !stopped) load_state(); });

load_state();
poll_timer = setInterval(() => {
  if (!stopped && !mutating && !document.hidden) load_state();
}, 5000);

function update_reminder_fields() {
  const mode = $("#taskRecurrence").value;
  const hourly = /^(hourly|every_\d+_hours)$/.test(mode);
  $("#taskRepeatFields").hidden=!hourly;
  if (!hourly) $("#taskCheckins").checked=false;
  $("#taskCheckinTimes").hidden=!hourly || !$("#taskCheckins").checked;
  const fixed = mode === "event" || mode === "three_times_daily";
  $("#taskEventFields").hidden = mode !== "event";
  $("#taskEventDate").required = mode === "event";
  $("#taskEscalationDays").required = mode === "event";
  $("#taskFixedTimes").hidden = mode !== "three_times_daily";
  $("#taskTime").disabled = fixed;
  $("#taskReminder").disabled = fixed;
  if (fixed) { $("#taskTime").value = "09:00"; $("#taskReminder").checked = true; }
  $("#taskTime").required = $("#taskReminder").checked;
}
$("#taskCheckins").addEventListener("change", update_reminder_fields);
$("#taskDate").addEventListener("change", () => {
  if ($("#taskRecurrence").value === "once" && (!$("#taskReminderEnd").value || $("#taskReminderEnd").value === editor_day)) $("#taskReminderEnd").value = $("#taskDate").value;
  editor_day = $("#taskDate").value;
});
$("#taskRecurrence").addEventListener("change", () => {
  const once = $("#taskRecurrence").value === "once";
  if (once && !$("#taskReminderEnd").value) $("#taskReminderEnd").value = $("#taskDate").value;
  if (!once && $("#taskReminderEnd").value === $("#taskDate").value) $("#taskReminderEnd").value = "";
  if (/^(hourly|every_\d+_hours)$/.test($("#taskRecurrence").value)) {
    $("#taskReminder").checked = true;
    if (!$("#taskTime").value) $("#taskTime").value = "09:00";
  }
  update_reminder_fields();
});
