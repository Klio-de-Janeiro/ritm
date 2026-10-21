/* Телефон и помощник: никакой отправки в ChatGPT без действия пользователя. */
(() => {
  "use strict";
  const host = document.querySelector("#settingsView .settings-layout > div");
  if (!host) return;
  const section = document.createElement("section");
  section.className = "panel settings-panel";
  section.innerHTML = `<h2>Устройства и синхронизация</h2>
    <p>Подключи несколько телефонов и планшетов Android к этому компьютеру — по USB или общей локальной сети. Дела и отметки объединяются. Компьютер должен быть включён, а Ритм запущен.</p>
    <p class="appearance-note">Обмен по HTTP, без шифрования. Включай только в доверенной частной сети. Не открывай порт 8769 в интернет. Темы и разрешения уведомлений остаются отдельными на каждом устройстве.</p>
    <button id="syncToggle" class="button primary">Загрузить настройки подключения</button>
    <div id="syncDetails" hidden><label>Адрес компьютера в локальной сети<select id="syncAddress"></select></label>
    <label>Код подключения<input id="syncToken" readonly type="password" autocomplete="off"></label>
    <button id="syncReveal" class="button subtle">Показать код</button>
    <button id="syncCopy" class="button subtle">Копировать подключение по сети</button>
    <button id="syncCopyUsb" class="button subtle">Копировать подключение по USB</button>
    <button id="syncRotate" class="button subtle">Сменить код</button>
    <p id="syncOtherAddresses" class="appearance-note">На каждом новом устройстве один раз вставь строку подключения в настройках Ритма. Добавление устройства не отключает остальные. Затем USB и сеть выбираются автоматически.</p>
    <details><summary>Как подключить</summary><p>По сети: подключи устройства к одной локальной сети, выбери адрес компьютера и скопируй подключение. На телефоне открой «Настройки → Обмен с компьютером» и вставь строку.</p><p>По USB: установи Android SDK Platform Tools на компьютер, включи USB-отладку на телефоне и разреши этот компьютер. Подключи кабель, скопируй USB-подключение и вставь его на телефоне. Так можно подключить несколько устройств одновременно, в том числе через USB-хаб.</p><p>Кнопка «Сменить код» потребует заново ввести его на всех устройствах.</p></details>
    <h3>Подключённые устройства</h3><div id="syncDevices" aria-live="polite"></div>
    <button id="syncRefresh" class="button subtle">Обновить список</button></div><p id="syncStatus" role="status"></p>`;
  host.prepend(section);
  let mobile = null;
  function showMobile(value) {
    mobile = value;
    $("#syncToggle").textContent = value.enabled ? "Выключить синхронизацию" : "Включить синхронизацию";
    $("#syncDetails").hidden = !value.enabled;
    const selected = $("#syncAddress").value;
    $("#syncAddress").innerHTML = value.addresses.map(address => `<option value="${escape_html(address)}">${escape_html(address)}</option>`).join("");
    $("#syncAddress").value = value.addresses.includes(selected) ? selected : value.addresses[0] || "";
    $("#syncCopy").disabled = !value.addresses.length;
    $("#syncToken").value = value.token;
    $("#syncStatus").textContent = value.error || (value.enabled ? `Обмен включён. Устройств в списке: ${(value.devices || []).length}.` : "Обмен выключен.");
    $("#syncDevices").innerHTML = (value.devices || []).length ? `<ul>${value.devices.map(device => `<li><strong>${escape_html(device.name)}</strong> · ${escape_html(device.transport)}<br><small>Последний обмен: ${escape_html(new Date(device.last_seen).toLocaleString("ru-RU"))}</small></li>`).join("")}</ul>` : "<p>Устройства появятся здесь после первого успешного обмена. Для старой версии Android обнови приложение.</p>";
  }
  $("#syncToggle").onclick = async () => {
    const button = $("#syncToggle"); button.disabled = true;
    try { showMobile(mobile ? await api("/mobile", "POST", {enabled: !mobile.enabled}) : await api("/mobile")); }
    catch (e) { toast(e.message, true); } finally { button.disabled = false; }
  };
  $("#syncRotate").onclick = async () => {
    try { showMobile(await api("/mobile", "POST", {rotate: true})); toast("Новый код нужно ввести на всех устройствах."); }
    catch (e) { toast(e.message, true); }
  };
  $("#syncReveal").onclick = () => {
    const field = $("#syncToken"); field.type = field.type === "password" ? "text" : "password";
    $("#syncReveal").textContent = field.type === "password" ? "Показать код" : "Скрыть код";
  };
  $("#syncCopy").onclick = async () => {
    try {
      if (!mobile?.addresses.length) throw new Error("Не найден адрес сети. Проверь подключение Wi-Fi.");
      await navigator.clipboard.writeText($("#syncAddress").value + "|" + mobile.token);
      toast("Адрес и код скопированы. Вставь их в поле подключения на телефоне.");
    } catch(e) { toast(e.message, true); }
  };
  $("#syncCopyUsb").onclick = async () => {
    try {
      await navigator.clipboard.writeText(mobile.usb_address + "|" + mobile.token);
      toast("USB-подключение скопировано. Вставь его на каждом новом устройстве.");
    } catch(e) { toast(e.message, true); }
  };
  $("#syncRefresh").onclick = async () => {
    try { showMobile(await api("/mobile")); } catch(e) { toast(e.message, true); }
  };
  setInterval(() => {
    if (mobile?.enabled && current_view === "settings" && !document.hidden) $("#syncRefresh").onclick();
  }, 10000);
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
