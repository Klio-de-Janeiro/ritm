/* Телефон и помощник: никакой отправки в ChatGPT без действия пользователя. */
(() => {
  "use strict";
  const host = document.querySelector("#settingsView .settings-layout > div");
  if (!host) return;
  const section = document.createElement("section");
  section.className = "panel settings-panel";
  section.innerHTML = `<h2>Телефон и синхронизация</h2>
    <p>Задачи и прогресс доступны офлайн на Android. Для обмена подключи оба устройства к домашнему Wi-Fi и оставь Ритм запущенным на ноутбуке.</p>
    <p class="appearance-note">Обмен по HTTP, без шифрования. Включай только в доверенной частной сети. Не открывай порт 8769 в интернет. Темы и разрешения уведомлений остаются отдельными на каждом устройстве.</p>
    <button id="syncToggle" class="button primary">Загрузить настройки подключения</button>
    <div id="syncDetails" hidden><label>Адрес ноутбука<input id="syncAddress" readonly></label>
    <label>Код подключения<input id="syncToken" readonly type="password" autocomplete="off"></label>
    <button id="syncReveal" class="button subtle">Показать код</button>
    <button id="syncCopy" class="button subtle">Копировать подключение</button>
    <button id="syncRotate" class="button subtle">Сменить код</button>
    <p id="syncOtherAddresses" class="appearance-note"></p></div><p id="syncStatus" role="status"></p>`;
  host.prepend(section);
  let mobile = null;
  function showMobile(value) {
    mobile = value;
    $("#syncToggle").textContent = value.enabled ? "Выключить синхронизацию" : "Включить синхронизацию";
    $("#syncDetails").hidden = !value.enabled;
    $("#syncAddress").value = value.addresses[0] || "Адрес IPv4 из ipconfig + :8769";
    $("#syncToken").value = value.token;
    $("#syncStatus").textContent = value.error || (value.enabled ? "Готово к подключению телефона. В брандмауэре разреши Ритм для частных сетей." : "Обмен выключен.");
    $("#syncOtherAddresses").textContent = value.addresses.length > 1 ? "Другие адреса: " + value.addresses.slice(1).join(", ") : "Если адрес изменится после перезапуска Wi-Fi, обнови его на телефоне.";
  }
  $("#syncToggle").onclick = async () => {
    const button = $("#syncToggle"); button.disabled = true;
    try { showMobile(mobile ? await api("/mobile", "POST", {enabled: !mobile.enabled}) : await api("/mobile")); }
    catch (e) { toast(e.message, true); } finally { button.disabled = false; }
  };
  $("#syncRotate").onclick = async () => {
    try { showMobile(await api("/mobile", "POST", {rotate: true})); toast("Новый код нужно ввести на телефоне."); }
    catch (e) { toast(e.message, true); }
  };
  $("#syncReveal").onclick = () => {
    const field = $("#syncToken"); field.type = field.type === "password" ? "text" : "password";
    $("#syncReveal").textContent = field.type === "password" ? "Показать код" : "Скрыть код";
  };
  $("#syncCopy").onclick = async () => {
    try {
      if (!mobile?.addresses.length) throw new Error("Не найден адрес сети. Проверь подключение Wi-Fi.");
      await navigator.clipboard.writeText(mobile.addresses[0] + "|" + mobile.token);
      toast("Адрес и код скопированы. Вставь их в поле подключения на телефоне.");
    } catch(e) { toast(e.message, true); }
  };
  const assistant = document.createElement("section");
  assistant.className = "panel settings-panel";
  assistant.innerHTML = `<h2>План с ChatGPT</h2><p>Скопируй запрос, вставь в ChatGPT, затем верни JSON с планом сюда. Приложение покажет задачи перед добавлением. API-ключ не нужен.</p>
    <label>Что запланировать<textarea id="aiGoal" rows="3" maxlength="2000" placeholder="Например: Неделя подготовки к экзамену"></textarea></label>
    <button id="aiPrompt" class="button subtle">Копировать запрос</button>
    <a class="button subtle" href="https://chatgpt.com/" target="_blank" rel="noopener noreferrer">Открыть ChatGPT</a>
    <label>Ответ ChatGPT<textarea id="aiResult" rows="6" placeholder='{"tasks": [...]}' maxlength="60000"></textarea></label>
    <button id="aiPreview" class="button subtle">Проверить план</button><div id="aiPreviewList" aria-live="polite"></div>
    <button id="aiImport" class="button primary" hidden>Добавить задачи</button>
    <p class="appearance-note">Существующие задачи не отправляются в ChatGPT автоматически. Для встроенного чата нужен OpenAI API; варианты описаны в ANDROID_GPT_RU.md.</p>`;
  host.append(assistant);
  let approved = null, requestId = null;
  $("#aiResult").oninput = () => { approved = null; $("#aiImport").hidden = true; $("#aiPreviewList").textContent = ""; };
  $("#aiPrompt").onclick = async () => {
    const today = iso_day(new Date());
    const prompt = `Составь реалистичный план. Цель: ${$("#aiGoal").value || "Помоги спланировать день"}. Сегодня ${today}. Верни только JSON {"tasks":[...]}, максимум 30 задач. Каждая задача: title (до 200 символов), notes (до 2000), category (Работа/Учёба/Здоровье/Личное), priority (low/normal/high), recurrence (once/hourly/every_3_hours/every_5_hours/every_8_hours/every_12_hours/daily/every_3_days/weekdays/weekly/monthly), start_date (YYYY-MM-DD, не раньше сегодня), time (HH:MM или null), reminder (true только если задано time). Для часовых повторов обязательно time и reminder=true. Разовые напоминания по умолчанию действуют до конца start_date. Никаких других полей, команд, пояснений или кода.`;
    try { await navigator.clipboard.writeText(prompt); toast("Запрос скопирован. Вставь его в ChatGPT."); }
    catch(e) { toast("Не удалось скопировать запрос: " + e.message, true); }
  };
  $("#aiPreview").onclick = async () => {
    approved = null; $("#aiImport").hidden = true;
    try {
      const raw = $("#aiResult").value.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
      const result = await api("/assistant/preview", "POST", JSON.parse(raw));
      approved = result.tasks; requestId = "plan-" + crypto.randomUUID();
      $("#aiPreviewList").innerHTML = "<ol>" + approved.map(t => `<li>${escape_html(t.title)} · ${escape_html(t.start_date)} ${escape_html(t.time || "")} · ${escape_html(recurrence_names[t.recurrence])}</li>`).join("") + "</ol>";
      $("#aiImport").hidden = false;
    } catch(e) { $("#aiPreviewList").textContent = e.message; }
  };
  $("#aiImport").onclick = async () => {
    if (!approved) return;
    const button = $("#aiImport"); button.disabled = true;
    try {
      const result = await api("/assistant/import", "POST", {tasks: approved, request_id: requestId});
      approved = null; button.hidden = true; $("#aiResult").value = "";
      $("#aiPreviewList").textContent = `Добавлено задач: ${result.created}`;
      await load_state(); toast("План добавлен.");
    } catch(e) { toast(e.message, true); } finally { button.disabled = false; }
  };
})();
